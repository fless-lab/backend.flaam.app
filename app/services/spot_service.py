from __future__ import annotations

"""
Spot service — §5.5, §3.8, §3.9.

Fonctionnalités :
- Recherche (city + catégorie + query)
- Détail + distribution fidélité
- Add / Remove sur le profil (limite 5 free / 12 premium)
- Check-in avec validation distance (<100 m)
- Niveaux de fidélité basés sur `checkin_count` :
  0 → declared, 2 → confirmed, 4 → regular, 6+ → regular_plus
- Popular (tri par total_checkins)
- Suggest (proposition par utilisateur, non vérifiée)
"""

import math
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

import structlog
from fastapi import status
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.onboarding import advance_onboarding, compute_completeness
from app.models.spot import Spot
from app.models.user import User
from app.models.user_spot import UserSpot

log = structlog.get_logger()


SPOT_LIMIT_FREE = 5
SPOT_LIMIT_PREMIUM = 12

# Distance max (mètres) pour un check-in valide
CHECKIN_MAX_DISTANCE_M = 100

# Seuils de fidélité (checkin_count → level)
# (instruction session 4 : 0=declared, 2=confirmed, 4=regular, 6=regular_plus)
FIDELITY_THRESHOLDS: list[tuple[int, str, float]] = [
    (6, "regular_plus", 1.0),
    (4, "regular", 0.85),
    (2, "confirmed", 0.7),
    (0, "declared", 0.5),
]


def _fidelity_for(count: int) -> tuple[str, float]:
    for threshold, level, score in FIDELITY_THRESHOLDS:
        if count >= threshold:
            return level, score
    return "declared", 0.5


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance en mètres entre deux points GPS."""
    R = 6_371_000  # rayon Terre en m
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _limit_for(user: User) -> int:
    return SPOT_LIMIT_PREMIUM if user.is_premium else SPOT_LIMIT_FREE


# ── Search ───────────────────────────────────────────────────────────

async def search_spots(
    city_id: UUID,
    db: AsyncSession,
    *,
    category: str | None = None,
    query: str | None = None,
    limit: int = 50,
) -> list[Spot]:
    stmt = select(Spot).where(Spot.city_id == city_id, Spot.is_active.is_(True))
    if category is not None:
        stmt = stmt.where(Spot.category == category)
    if query:
        stmt = stmt.where(Spot.name.ilike(f"%{query}%"))
    stmt = stmt.order_by(Spot.total_checkins.desc(), Spot.name).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ── Detail ───────────────────────────────────────────────────────────

async def get_spot_detail(spot_id: UUID, db: AsyncSession) -> dict:
    spot = await db.get(Spot, spot_id)
    if spot is None or not spot.is_active:
        raise AppException(status.HTTP_404_NOT_FOUND, "spot_not_found")

    # Distribution fidélité sur UserSpot pour ce spot
    result = await db.execute(
        select(UserSpot.fidelity_level, func.count())
        .where(UserSpot.spot_id == spot_id)
        .group_by(UserSpot.fidelity_level)
    )
    distribution = {
        "declared": 0, "confirmed": 0, "regular": 0, "regular_plus": 0
    }
    for level, count in result.all():
        distribution[level] = count

    return {
        "id": spot.id,
        "name": spot.name,
        "category": spot.category,
        "city_id": spot.city_id,
        "latitude": spot.latitude,
        "longitude": spot.longitude,
        "address": spot.address,
        "total_checkins": spot.total_checkins,
        "total_users": spot.total_users,
        "is_verified": spot.is_verified,
        "fidelity_distribution": distribution,
    }


# ── Add / Remove ─────────────────────────────────────────────────────

async def _fetch_user_spots(user: User, db: AsyncSession) -> list[UserSpot]:
    result = await db.execute(
        select(UserSpot).where(UserSpot.user_id == user.id)
    )
    return list(result.scalars().all())


async def add_spot(user: User, spot_id: UUID, db: AsyncSession) -> UserSpot:
    spot = await db.get(Spot, spot_id)
    if spot is None or not spot.is_active:
        raise AppException(status.HTTP_404_NOT_FOUND, "spot_not_found")

    current = await _fetch_user_spots(user, db)
    if any(us.spot_id == spot_id for us in current):
        raise AppException(status.HTTP_400_BAD_REQUEST, "spot_already_added")

    limit = _limit_for(user)
    if len(current) >= limit:
        raise AppException(
            status.HTTP_400_BAD_REQUEST,
            f"max_spots_reached:{limit}",
        )

    us = UserSpot(
        user_id=user.id,
        spot_id=spot_id,
        checkin_count=0,
        fidelity_level="declared",
        fidelity_score=0.5,
        is_visible=True,
    )
    db.add(us)
    spot.total_users = (spot.total_users or 0) + 1

    # Onboarding SPOTS est satisfait dès 1 spot ajouté
    try:
        user.user_spots.append(us)
    except Exception:
        pass
    if user.profile is not None:
        score, _ = compute_completeness(user, user.profile)
        user.profile.profile_completeness = score
    advance_onboarding(user)

    await db.commit()
    await db.refresh(us)
    log.info("spot_added", user_id=str(user.id), spot_id=str(spot_id))
    return us


async def remove_spot(user: User, spot_id: UUID, db: AsyncSession) -> None:
    result = await db.execute(
        select(UserSpot).where(
            UserSpot.user_id == user.id, UserSpot.spot_id == spot_id
        )
    )
    us = result.scalar_one_or_none()
    if us is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "user_spot_not_found")

    spot = await db.get(Spot, spot_id)
    if spot is not None:
        spot.total_users = max(0, (spot.total_users or 0) - 1)

    await db.delete(us)
    try:
        user.user_spots.remove(us)
    except (ValueError, AttributeError):
        pass
    if user.profile is not None:
        score, _ = compute_completeness(user, user.profile)
        user.profile.profile_completeness = score
    advance_onboarding(user)

    await db.commit()
    log.info("spot_removed", user_id=str(user.id), spot_id=str(spot_id))


# ── Visibility ───────────────────────────────────────────────────────

async def toggle_spot_visibility(
    user: User, spot_id: UUID, is_visible: bool, db: AsyncSession
) -> UserSpot:
    result = await db.execute(
        select(UserSpot).where(
            UserSpot.user_id == user.id, UserSpot.spot_id == spot_id
        )
    )
    us = result.scalar_one_or_none()
    if us is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "user_spot_not_found")
    us.is_visible = is_visible
    await db.commit()
    await db.refresh(us)
    return us


# ── Check-in ─────────────────────────────────────────────────────────

async def check_in(
    user: User,
    spot_id: UUID,
    latitude: float,
    longitude: float,
    db: AsyncSession,
) -> dict:
    spot = await db.get(Spot, spot_id)
    if spot is None or not spot.is_active:
        raise AppException(status.HTTP_404_NOT_FOUND, "spot_not_found")

    distance = _haversine_m(latitude, longitude, spot.latitude, spot.longitude)
    if distance > CHECKIN_MAX_DISTANCE_M:
        raise AppException(
            status.HTTP_400_BAD_REQUEST,
            f"too_far:{int(distance)}",
        )

    # UserSpot : soit existant, soit on le crée automatiquement (ajout
    # implicite via check-in, avec respect de la limite).
    result = await db.execute(
        select(UserSpot).where(
            UserSpot.user_id == user.id, UserSpot.spot_id == spot_id
        )
    )
    us = result.scalar_one_or_none()
    if us is None:
        current = await _fetch_user_spots(user, db)
        limit = _limit_for(user)
        if len(current) >= limit:
            raise AppException(
                status.HTTP_400_BAD_REQUEST,
                f"max_spots_reached:{limit}",
            )
        us = UserSpot(
            user_id=user.id,
            spot_id=spot_id,
            checkin_count=0,
            fidelity_level="declared",
            fidelity_score=0.5,
            first_checkin_at=datetime.now(timezone.utc),
        )
        db.add(us)
        spot.total_users = (spot.total_users or 0) + 1

    previous_level = us.fidelity_level
    us.checkin_count = (us.checkin_count or 0) + 1
    us.last_checkin_at = datetime.now(timezone.utc)
    if us.first_checkin_at is None:
        us.first_checkin_at = us.last_checkin_at

    new_level, new_score = _fidelity_for(us.checkin_count)
    us.fidelity_level = new_level
    us.fidelity_score = new_score
    spot.total_checkins = (spot.total_checkins or 0) + 1

    await db.commit()
    await db.refresh(us)

    # Side-effect : si l'user est en mode voyage, profite des coords
    # GPS du check-in pour confirmer sa présence dans la ville de
    # destination (no-op silencieux si trop loin / pas en voyage).
    from app.services import travel_service as _travel
    await _travel.try_confirm_travel(user, latitude, longitude, db)

    log.info(
        "spot_checkin",
        user_id=str(user.id),
        spot_id=str(spot_id),
        count=us.checkin_count,
        level=new_level,
    )
    return {
        "spot_id": spot.id,
        "spot_name": spot.name,
        "checkin_count": us.checkin_count,
        "fidelity_level": new_level,
        "previous_level": previous_level,
        "level_upgraded": new_level != previous_level,
    }


# ── Popular ──────────────────────────────────────────────────────────

async def get_popular_spots(
    city_id: UUID, db: AsyncSession, *, limit: int = 20
) -> list[Spot]:
    stmt = (
        select(Spot)
        .where(Spot.city_id == city_id, Spot.is_active.is_(True))
        .order_by(Spot.total_checkins.desc(), Spot.total_users.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ── Suggest (user-submitted) ─────────────────────────────────────────

async def suggest_spot(
    user: User,
    *,
    name: str,
    category: str,
    city_id: UUID,
    latitude: float,
    longitude: float,
    address: str | None,
    db: AsyncSession,
) -> Spot:
    geom = from_shape(Point(longitude, latitude), srid=4326)
    spot = Spot(
        name=name.strip(),
        category=category,
        city_id=city_id,
        location=geom,
        latitude=latitude,
        longitude=longitude,
        address=address,
        is_verified=False,
        is_active=True,
        created_by_user_id=user.id,
    )
    db.add(spot)
    await db.commit()
    await db.refresh(spot)
    log.info(
        "spot_suggested",
        user_id=str(user.id),
        spot_id=str(spot.id),
        name=name,
    )
    return spot


# ── Serialization helpers ────────────────────────────────────────────

def serialize_spot(spot: Spot) -> dict:
    return {
        "id": spot.id,
        "name": spot.name,
        "category": spot.category,
        "city_id": spot.city_id,
        "latitude": spot.latitude,
        "longitude": spot.longitude,
        "address": spot.address,
        "total_checkins": spot.total_checkins,
        "total_users": spot.total_users,
        "is_verified": spot.is_verified,
    }


def serialize_spots(spots: Iterable[Spot]) -> list[dict]:
    return [serialize_spot(s) for s in spots]


__all__ = [
    "SPOT_LIMIT_FREE",
    "SPOT_LIMIT_PREMIUM",
    "CHECKIN_MAX_DISTANCE_M",
    "FIDELITY_THRESHOLDS",
    "search_spots",
    "get_spot_detail",
    "add_spot",
    "remove_spot",
    "toggle_spot_visibility",
    "check_in",
    "get_popular_spots",
    "suggest_spot",
    "serialize_spot",
    "serialize_spots",
]
