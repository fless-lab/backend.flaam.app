from __future__ import annotations

"""
Quartier service — §5.4.

Règles :
- 4 types de relation : lives / works / hangs / interested.
- Limites par type et par tier : lives=2, works=2, hangs=4,
  interested=3 free / 6 premium.
- Le quartier doit appartenir à la ville de l'utilisateur.
- Pas de doublon (user_id, quartier_id, relation_type).
- Après modification, on recompute le score de complétude et on
  avance l'onboarding si l'étape QUARTIERS est satisfaite.
"""

from uuid import UUID

import structlog
from fastapi import status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.onboarding import advance_onboarding, compute_completeness
from app.models.quartier import Quartier
from app.models.quartier_proximity import QuartierProximity
from app.models.user import User
from app.models.user_quartier import UserQuartier

log = structlog.get_logger()


FREE_LIMITS: dict[str, int] = {
    "lives": 2,
    "works": 2,
    "interested": 3,
}
PREMIUM_OVERRIDES: dict[str, int] = {"interested": 6}


def _limit_for(relation_type: str, is_premium: bool) -> int:
    if is_premium and relation_type in PREMIUM_OVERRIDES:
        return PREMIUM_OVERRIDES[relation_type]
    return FREE_LIMITS[relation_type]


# ── Listing ──────────────────────────────────────────────────────────

async def list_quartiers_by_city(
    city_id: UUID, db: AsyncSession
) -> list[Quartier]:
    result = await db.execute(
        select(Quartier).where(Quartier.city_id == city_id).order_by(Quartier.name)
    )
    return list(result.scalars().all())


# ── Add ──────────────────────────────────────────────────────────────

async def add_quartier_to_profile(
    user: User,
    quartier_id: UUID,
    relation_type: str,
    is_primary: bool,
    db: AsyncSession,
) -> UserQuartier:
    if user.city_id is None:
        raise AppException(
            status.HTTP_400_BAD_REQUEST, "city_not_selected"
        )

    quartier = await db.get(Quartier, quartier_id)
    if quartier is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "quartier_not_found")

    if quartier.city_id != user.city_id:
        raise AppException(
            status.HTTP_400_BAD_REQUEST, "quartier_not_in_city"
        )

    # Doublon : on est idempotent. Si la paire (user, quartier, relation)
    # existe déjà, on renvoie la row existante (avec mise à jour
    # éventuelle de is_primary) au lieu de 400. Ça évite que le mobile
    # ait à faire la diff parfaite avant chaque save — il peut juste
    # POSTer la liste désirée.
    existing_same = await db.execute(
        select(UserQuartier).where(
            UserQuartier.user_id == user.id,
            UserQuartier.quartier_id == quartier_id,
            UserQuartier.relation_type == relation_type,
        )
    )
    existing_uq = existing_same.scalar_one_or_none()
    if existing_uq is not None:
        if is_primary and relation_type == "lives":
            for uq in (user.user_quartiers or []):
                if uq.relation_type == "lives" and uq.is_primary:
                    uq.is_primary = False
            existing_uq.is_primary = True
            await db.commit()
            await db.refresh(existing_uq)
        return existing_uq

    # Compter les existants du même type
    existing_of_type = await db.execute(
        select(UserQuartier).where(
            UserQuartier.user_id == user.id,
            UserQuartier.relation_type == relation_type,
        )
    )
    current_count = len(list(existing_of_type.scalars().all()))
    limit = _limit_for(relation_type, user.is_premium)
    if current_count >= limit:
        raise AppException(
            status.HTTP_400_BAD_REQUEST,
            f"max_quartiers_reached:{relation_type}:{limit}",
        )

    # Si is_primary=True sur un "lives", on dé-flag les autres "lives"
    if is_primary and relation_type == "lives":
        for uq in (user.user_quartiers or []):
            if uq.relation_type == "lives" and uq.is_primary:
                uq.is_primary = False

    uq = UserQuartier(
        user_id=user.id,
        quartier_id=quartier_id,
        relation_type=relation_type,
        is_primary=is_primary,
    )
    db.add(uq)

    # Onboarding + completeness
    try:
        user.user_quartiers.append(uq)
    except Exception:
        pass
    if user.profile is not None:
        score, _ = compute_completeness(user, user.profile)
        user.profile.profile_completeness = score
    advance_onboarding(user)

    await db.commit()
    await db.refresh(uq)
    log.info(
        "quartier_added",
        user_id=str(user.id),
        quartier_id=str(quartier_id),
        relation_type=relation_type,
    )
    return uq


# ── Remove ───────────────────────────────────────────────────────────

async def remove_quartier(
    user: User,
    quartier_id: UUID,
    relation_type: str,
    db: AsyncSession,
) -> None:
    result = await db.execute(
        select(UserQuartier).where(
            UserQuartier.user_id == user.id,
            UserQuartier.quartier_id == quartier_id,
            UserQuartier.relation_type == relation_type,
        )
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "user_quartier_not_found")

    await db.delete(target)

    try:
        user.user_quartiers.remove(target)
    except (ValueError, AttributeError):
        pass
    if user.profile is not None:
        score, _ = compute_completeness(user, user.profile)
        user.profile.profile_completeness = score
    advance_onboarding(user)

    await db.commit()
    log.info(
        "quartier_removed",
        user_id=str(user.id),
        quartier_id=str(quartier_id),
        relation_type=relation_type,
    )


# ── My quartiers (grouped) ───────────────────────────────────────────

async def get_my_quartiers(user: User, db: AsyncSession) -> dict:
    result = await db.execute(
        select(UserQuartier).where(UserQuartier.user_id == user.id)
    )
    entries = list(result.scalars().all())

    grouped: dict[str, list[dict]] = {
        "lives": [], "works": [], "interested": []
    }
    for uq in entries:
        # Skip les éventuelles rows legacy 'hangs' (drop en migration mais
        # garde-fou si la migration n'a pas encore été appliquée).
        if uq.relation_type not in grouped:
            continue
        q = uq.quartier
        grouped[uq.relation_type].append(
            {
                "user_quartier_id": str(uq.id),
                "quartier_id": str(uq.quartier_id),
                "name": q.name if q else None,
                "latitude": q.latitude if q else None,
                "longitude": q.longitude if q else None,
                "is_primary": uq.is_primary,
            }
        )

    limits: dict[str, dict] = {}
    for rt, free_limit in FREE_LIMITS.items():
        current = len(grouped[rt])
        entry = {"current": current, "max": free_limit}
        if rt in PREMIUM_OVERRIDES:
            entry["max_premium"] = PREMIUM_OVERRIDES[rt]
        limits[rt] = entry

    return {**grouped, "limits": limits}


# ── Nearby (graphe de proximité) ─────────────────────────────────────

async def get_nearby_quartiers(
    quartier_id: UUID,
    db: AsyncSession,
    limit: int = 10,
) -> dict:
    quartier = await db.get(Quartier, quartier_id)
    if quartier is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "quartier_not_found")

    # Le graphe stocke (a, b) avec a < b. On cherche les deux côtés.
    result = await db.execute(
        select(QuartierProximity).where(
            or_(
                QuartierProximity.quartier_a_id == quartier_id,
                QuartierProximity.quartier_b_id == quartier_id,
            )
        )
    )
    edges = list(result.scalars().all())

    nearby: list[dict] = []
    for edge in edges:
        other_id = (
            edge.quartier_b_id
            if edge.quartier_a_id == quartier_id
            else edge.quartier_a_id
        )
        other = await db.get(Quartier, other_id)
        if other is None:
            continue
        nearby.append(
            {
                "id": other.id,
                "name": other.name,
                "proximity": round(edge.proximity_score, 4),
                "distance_km": round(edge.distance_km, 2),
            }
        )

    nearby.sort(key=lambda n: n["proximity"], reverse=True)
    nearby = nearby[:limit]

    return {
        "quartier": {
            "id": quartier.id,
            "name": quartier.name,
            "latitude": quartier.latitude,
            "longitude": quartier.longitude,
        },
        "nearby": nearby,
    }


__all__ = [
    "FREE_LIMITS",
    "PREMIUM_OVERRIDES",
    "list_quartiers_by_city",
    "add_quartier_to_profile",
    "remove_quartier",
    "get_my_quartiers",
    "get_nearby_quartiers",
]
