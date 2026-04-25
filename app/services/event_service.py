from __future__ import annotations

"""
Event service (§5.9 + MàJ 8 Porte 3).

Endpoints user-facing :
- list events par ville + fenêtre temporelle (status published/full)
- détail d'un event
- register / unregister avec gestion capacité + lifecycle
- matches-preview : top 5 profils compatibles parmi les inscrits
- stats anonymes publiques (page web)

Les endpoints admin (create, update, publish, cancel) sont en Session 10.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import structlog
from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import FlaamError
from app.core.exceptions import AppException
from app.models.city import City
from app.models.event import Event
from app.models.event_registration import EventRegistration
from app.models.photo import Photo
from app.models.profile import Profile
from app.models.quartier import Quartier
from app.models.spot import Spot
from app.models.user import User
from app.models.user_quartier import UserQuartier

log = structlog.get_logger()


# Catégorie event → tags suggérés pour pré-cocher lors de la conversion
# ghost → app (MàJ 8 §2). L'utilisateur peut décocher.
EVENT_CATEGORY_TO_TAGS: dict[str, list[str]] = {
    "afterwork": ["afterwork", "sortir", "networking"],
    "sport": ["sport", "fitness", "outdoor"],
    "brunch": ["food", "brunch", "chill"],
    "cultural": ["culture", "art", "sortir"],
    "networking": ["networking", "pro", "business"],
    "workshop": ["apprendre", "workshop", "creatif"],
    "outdoor": ["outdoor", "nature", "aventure"],
}


def _category_tags(category: str) -> list[str]:
    return EVENT_CATEGORY_TO_TAGS.get(category, [])


# ══════════════════════════════════════════════════════════════════════
# List / Detail
# ══════════════════════════════════════════════════════════════════════


async def list_events(
    *,
    city_id: UUID | None,
    from_date: datetime | None,
    to_date: datetime | None,
    db: AsyncSession,
) -> list[dict]:
    """Events visibles = status in (published, full)."""
    stmt = (
        select(Event, Spot.name)
        .join(Spot, Spot.id == Event.spot_id)
        .where(Event.status.in_(("published", "full")))
        .where(Event.is_active.is_(True))
        .order_by(Event.starts_at.asc())
    )
    if city_id is not None:
        stmt = stmt.where(Event.city_id == city_id)
    if from_date is not None:
        stmt = stmt.where(Event.starts_at >= from_date)
    if to_date is not None:
        stmt = stmt.where(Event.starts_at <= to_date)

    rows = (await db.execute(stmt)).all()
    return [
        {
            "id": ev.id,
            "title": ev.title,
            "description": ev.description,
            "category": ev.category,
            "status": ev.status,
            "starts_at": ev.starts_at,
            "ends_at": ev.ends_at,
            "spot_id": ev.spot_id,
            "spot_name": spot_name,
            "city_id": ev.city_id,
            "max_attendees": ev.max_attendees,
            "current_attendees": ev.current_attendees,
            "slug": ev.slug,
        }
        for ev, spot_name in rows
    ]


async def get_event_detail(
    event_id: UUID, user: User, db: AsyncSession, lang: str = "fr"
) -> dict:
    ev = await db.get(Event, event_id)
    if ev is None or not ev.is_active:
        raise FlaamError("event_not_found", 404, lang)

    spot = await db.get(Spot, ev.spot_id)
    reg_row = await db.execute(
        select(EventRegistration).where(
            EventRegistration.event_id == event_id,
            EventRegistration.user_id == user.id,
        )
    )
    reg = reg_row.scalar_one_or_none()

    return {
        "id": ev.id,
        "title": ev.title,
        "description": ev.description,
        "category": ev.category,
        "status": ev.status,
        "starts_at": ev.starts_at,
        "ends_at": ev.ends_at,
        "spot_id": ev.spot_id,
        "spot_name": spot.name if spot else None,
        "city_id": ev.city_id,
        "max_attendees": ev.max_attendees,
        "current_attendees": ev.current_attendees,
        "slug": ev.slug,
        "is_sponsored": ev.is_sponsored,
        "sponsor_name": ev.sponsor_name,
        "is_registered": reg is not None,
        "registration_status": reg.status if reg else None,
    }


# ══════════════════════════════════════════════════════════════════════
# Register / Unregister
# ══════════════════════════════════════════════════════════════════════


async def register_to_event(
    event_id: UUID,
    user: User,
    db: AsyncSession,
    *,
    via: str = "app",
    lang: str = "fr",
) -> dict:
    """
    Inscrit un user à un event. Gère la capacité :
    - incrémente current_attendees
    - passe en "full" si plafond atteint
    """
    ev = await db.get(Event, event_id)
    if ev is None or not ev.is_active:
        raise FlaamError("event_not_found", 404, lang)
    if ev.status == "cancelled":
        raise AppException(status.HTTP_400_BAD_REQUEST, "event_cancelled")
    if ev.status in ("completed",):
        raise AppException(status.HTTP_400_BAD_REQUEST, "event_ended")
    if ev.status == "draft":
        raise FlaamError("event_not_found", 404, lang)

    existing = (
        await db.execute(
            select(EventRegistration).where(
                EventRegistration.event_id == event_id,
                EventRegistration.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {
            "status": "already_registered",
            "event_id": ev.id,
            "current_attendees": ev.current_attendees,
            "max_attendees": ev.max_attendees,
        }

    if ev.max_attendees is not None and ev.current_attendees >= ev.max_attendees:
        raise FlaamError("event_full", 409, lang)

    reg = EventRegistration(
        event_id=event_id,
        user_id=user.id,
        status="registered",
        registered_via=via,
        suggested_tags=_category_tags(ev.category),
    )
    db.add(reg)
    ev.current_attendees += 1
    if ev.max_attendees is not None and ev.current_attendees >= ev.max_attendees:
        ev.status = "full"

    await db.commit()
    log.info(
        "event_registered",
        event_id=str(event_id),
        user_id=str(user.id),
        via=via,
    )
    return {
        "status": "registered",
        "event_id": ev.id,
        "current_attendees": ev.current_attendees,
        "max_attendees": ev.max_attendees,
    }


async def unregister_from_event(
    event_id: UUID, user: User, db: AsyncSession, lang: str = "fr"
) -> dict:
    ev = await db.get(Event, event_id)
    if ev is None:
        raise FlaamError("event_not_found", 404, lang)

    existing = (
        await db.execute(
            select(EventRegistration).where(
                EventRegistration.event_id == event_id,
                EventRegistration.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "registration_not_found")
    if existing.status == "checked_in":
        raise AppException(
            status.HTTP_400_BAD_REQUEST, "cannot_unregister_checked_in"
        )

    await db.delete(existing)
    ev.current_attendees = max(0, ev.current_attendees - 1)
    if ev.status == "full" and (
        ev.max_attendees is None or ev.current_attendees < ev.max_attendees
    ):
        ev.status = "published"

    await db.commit()
    log.info(
        "event_unregistered",
        event_id=str(event_id),
        user_id=str(user.id),
    )
    return {
        "status": "unregistered",
        "event_id": ev.id,
        "current_attendees": ev.current_attendees,
    }


# ══════════════════════════════════════════════════════════════════════
# Matches preview
# ══════════════════════════════════════════════════════════════════════


async def matches_preview(
    event_id: UUID,
    user: User,
    db: AsyncSession,
    *,
    top_n: int = 5,
    lang: str = "fr",
) -> dict:
    """
    Top N profils compatibles parmi les inscrits à l'event.
    On calcule geo + lifestyle (L1 hard filters + L2 + L3) pour les
    co-inscrits, on trie par score combiné, on retourne un aperçu minimal.
    """
    from app.services.matching_engine import (
        geo_scorer,
        hard_filters,
        lifestyle_scorer,
    )

    ev = await db.get(Event, event_id)
    if ev is None or not ev.is_active:
        raise FlaamError("event_not_found", 404, lang)

    # Full-load user (profile + quartiers + spots) pour les scorers.
    full_user = await _load_user_full(user.id, db)
    if full_user is None or full_user.profile is None:
        return {"total_compatible": 0, "top": []}

    # Co-inscrits (hors soi)
    reg_rows = await db.execute(
        select(EventRegistration.user_id).where(
            EventRegistration.event_id == event_id,
            EventRegistration.user_id != user.id,
        )
    )
    candidate_ids = [r[0] for r in reg_rows.all()]
    if not candidate_ids:
        return {"total_compatible": 0, "top": []}

    # Hard filters : on intersecte avec les co-inscrits
    filtered = await hard_filters.apply_hard_filters(full_user, db)
    filtered_set = set(filtered)
    compatible = [c for c in candidate_ids if c in filtered_set]
    if not compatible:
        return {"total_compatible": 0, "top": []}

    await geo_scorer.load_proximity_cache(full_user.city_id, db)
    # Config minimal pour les scorers (defaults baked in)
    config: dict = {}
    geo = await geo_scorer.compute_geo_scores(
        full_user, compatible, config, db
    )
    life = await lifestyle_scorer.compute_lifestyle_scores(
        full_user, compatible, config, db
    )

    combined: list[tuple[UUID, float, float]] = []
    for cid in compatible:
        g = geo.get(cid, 0.0)
        lif = life.get(cid, 0.0)
        combined.append((cid, g, lif))
    combined.sort(key=lambda t: (t[1] + t[2]), reverse=True)
    top = combined[:top_n]

    # Resolve display_name + primary photo pour les top
    top_ids = [cid for cid, _, _ in top]
    profile_rows = await db.execute(
        select(Profile).where(Profile.user_id.in_(top_ids))
    )
    profiles = {p.user_id: p for p in profile_rows.scalars().all()}

    photo_rows = await db.execute(
        select(Photo)
        .where(Photo.user_id.in_(top_ids))
        .order_by(Photo.user_id, Photo.display_order.asc())
    )
    primary_photo: dict[UUID, str] = {}
    for ph in photo_rows.scalars().all():
        primary_photo.setdefault(ph.user_id, ph.url)

    top_list = []
    for cid, g, lif in top:
        p = profiles.get(cid)
        top_list.append(
            {
                "user_id": cid,
                "display_name": p.display_name if p else "",
                "primary_photo_url": primary_photo.get(cid),
                "geo_score": int(round(g * 100)),
                "lifestyle_score": int(round(lif * 100)),
            }
        )

    return {"total_compatible": len(compatible), "top": top_list}


async def _load_user_full(user_id: UUID, db: AsyncSession) -> User | None:
    from sqlalchemy.orm import selectinload

    stmt = (
        select(User)
        .options(
            selectinload(User.profile),
            selectinload(User.user_quartiers),
            selectinload(User.user_spots),
        )
        .where(User.id == user_id)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


# ══════════════════════════════════════════════════════════════════════
# Stats publiques (page web event)
# ══════════════════════════════════════════════════════════════════════


async def get_event_stats(event_id: UUID, db: AsyncSession) -> dict:
    """
    Stats anonymes pour la page web — pas de noms, pas de profils.
    Compteurs + top quartiers (agrégé).
    """
    ev = await db.get(Event, event_id)
    if ev is None or not ev.is_active:
        raise AppException(status.HTTP_404_NOT_FOUND, "event_not_found")

    reg_count_row = await db.execute(
        select(func.count())
        .select_from(EventRegistration)
        .where(EventRegistration.event_id == event_id)
    )
    registered_count = int(reg_count_row.scalar_one() or 0)

    checkin_count_row = await db.execute(
        select(func.count())
        .select_from(EventRegistration)
        .where(
            EventRegistration.event_id == event_id,
            EventRegistration.status == "checked_in",
        )
    )
    checked_in_count = int(checkin_count_row.scalar_one() or 0)

    # Top quartiers des inscrits : jointure user_quartiers + quartiers
    q_rows = await db.execute(
        select(Quartier.name, func.count(func.distinct(UserQuartier.user_id)))
        .join(UserQuartier, UserQuartier.quartier_id == Quartier.id)
        .join(
            EventRegistration,
            EventRegistration.user_id == UserQuartier.user_id,
        )
        .where(EventRegistration.event_id == event_id)
        .group_by(Quartier.name)
        .order_by(func.count(func.distinct(UserQuartier.user_id)).desc())
        .limit(5)
    )
    breakdown = {name: int(count) for name, count in q_rows.all()}

    spots_left = None
    if ev.max_attendees is not None:
        spots_left = max(0, ev.max_attendees - ev.current_attendees)

    return {
        "event_id": ev.id,
        "event_name": ev.title,
        "event_date": ev.starts_at,
        "registered_count": registered_count,
        "checked_in_count": checked_in_count,
        "spots_left": spots_left,
        "quartier_breakdown": breakdown,
    }


# ══════════════════════════════════════════════════════════════════════
# Check-in QR
# ══════════════════════════════════════════════════════════════════════


async def checkin_event(
    event_id: UUID,
    qr_code: str,
    db: AsyncSession,
    lang: str = "fr",
) -> dict:
    """
    Check-in via QR HMAC signé.
    - Vérifie la signature (timing-safe)
    - Vérifie que le QR correspond à cet event
    - Passe EventRegistration.status → checked_in
    - Promeut ghost → pre_registered
    - Ajoute automatiquement le spot de l'event aux user_spots
    """
    from app.core.security import qr_code_hash, verify_event_qr
    from app.models.user_spot import UserSpot

    decoded = verify_event_qr(qr_code)
    if decoded is None:
        raise AppException(status.HTTP_403_FORBIDDEN, "invalid_qr_signature")
    ev_id_str, user_id_str = decoded
    if str(event_id) != ev_id_str:
        raise AppException(status.HTTP_403_FORBIDDEN, "qr_event_mismatch")

    try:
        scanned_user_id = UUID(user_id_str)
    except ValueError:
        raise AppException(status.HTTP_403_FORBIDDEN, "invalid_qr_signature")

    ev = await db.get(Event, event_id)
    if ev is None or not ev.is_active:
        raise FlaamError("event_not_found", 404, lang)

    scanned_user = await db.get(User, scanned_user_id)
    if scanned_user is None:
        raise FlaamError("user_not_found", 404, lang)

    reg_row = await db.execute(
        select(EventRegistration).where(
            EventRegistration.event_id == event_id,
            EventRegistration.user_id == scanned_user_id,
        )
    )
    reg = reg_row.scalar_one_or_none()
    if reg is None:
        raise AppException(
            status.HTTP_404_NOT_FOUND, "registration_not_found"
        )

    qhash = qr_code_hash(qr_code)
    if reg.status == "checked_in":
        # Idempotent : deuxième scan du même QR → même résultat
        count_row = await db.execute(
            select(func.count())
            .select_from(EventRegistration)
            .where(
                EventRegistration.event_id == event_id,
                EventRegistration.status == "checked_in",
            )
        )
        return {
            "status": "checked_in",
            "event_id": ev.id,
            "user_id": scanned_user_id,
            "attendees_count": int(count_row.scalar_one() or 0),
        }

    reg.status = "checked_in"
    reg.checked_in_at = datetime.now(timezone.utc)
    reg.qr_code_hash = qhash

    # Ghost → pre_registered
    if scanned_user.onboarding_step == "ghost":
        scanned_user.onboarding_step = "pre_registered"

    # Ajoute le spot de l'event aux user_spots (si pas déjà présent)
    existing_us = await db.execute(
        select(UserSpot).where(
            UserSpot.user_id == scanned_user_id,
            UserSpot.spot_id == ev.spot_id,
        )
    )
    if existing_us.scalar_one_or_none() is None:
        db.add(
            UserSpot(
                user_id=scanned_user_id,
                spot_id=ev.spot_id,
                checkin_count=0,
                fidelity_level="declared",
                fidelity_score=0.5,
                is_visible=True,
            )
        )

    await db.commit()

    count_row = await db.execute(
        select(func.count())
        .select_from(EventRegistration)
        .where(
            EventRegistration.event_id == event_id,
            EventRegistration.status == "checked_in",
        )
    )
    log.info(
        "event_checkin",
        event_id=str(event_id),
        user_id=str(scanned_user_id),
    )
    return {
        "status": "checked_in",
        "event_id": ev.id,
        "user_id": scanned_user_id,
        "attendees_count": int(count_row.scalar_one() or 0),
    }


# ── Self check-in GPS (l'user prouve sa présence physique) ──────────


async def self_checkin_event(
    event_id: UUID,
    user: User,
    lat: float,
    lng: float,
    db: AsyncSession,
) -> dict:
    """
    Vérifie : event existe, pas terminé +2h, lat/lng <200m du venue.
    Crée un EventCheckin et alimente flame/nearby. Idempotent à la
    minute (évite spam). Met aussi à jour user.last_lat/lng.
    """
    import math
    from app.models.event_checkin import EventCheckin

    ev = await db.get(Event, event_id)
    if ev is None:
        raise AppException(404, "event_not_found")

    now = datetime.now(timezone.utc)
    starts = ev.starts_at
    ends = ev.ends_at or starts + timedelta(hours=4)
    if starts.tzinfo is None:
        starts = starts.replace(tzinfo=timezone.utc)
    if ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)
    if now < starts - timedelta(hours=1):
        raise AppException(400, "event_not_started_yet")
    if now > ends + timedelta(hours=2):
        raise AppException(400, "event_ended")

    spot = await db.get(Spot, ev.spot_id)
    if spot is None:
        raise AppException(500, "spot_missing")

    R = 6_371_000
    dlat = math.radians(spot.latitude - lat)
    dlng = math.radians(spot.longitude - lng)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat))
        * math.cos(math.radians(spot.latitude))
        * math.sin(dlng / 2) ** 2
    )
    distance = int(R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))
    if distance > 200:
        raise AppException(400, f"too_far_from_venue:{distance}m")

    recent = await db.execute(
        select(EventCheckin).where(
            EventCheckin.user_id == user.id,
            EventCheckin.event_id == event_id,
            EventCheckin.at >= now - timedelta(minutes=1),
        ),
    )
    if recent.first() is None:
        ck = EventCheckin(
            user_id=user.id, event_id=event_id,
            lat=lat, lng=lng, verified=True, at=now,
        )
        db.add(ck)

    user.last_lat = lat
    user.last_lng = lng
    user.last_location_at = now
    await db.commit()

    return {
        "status": "checked_in",
        "event_id": event_id,
        "user_id": user.id,
        "distance_to_venue_m": distance,
    }


async def list_present(event_id: UUID, user: User, db: AsyncSession) -> dict:
    """
    Liste les users actuellement présents (checked-in <2h). Free voit
    le count seulement, premium voit la liste user_ids.
    """
    from app.models.event_checkin import EventCheckin

    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    result = await db.execute(
        select(EventCheckin.user_id, func.max(EventCheckin.at))
        .where(
            EventCheckin.event_id == event_id,
            EventCheckin.at >= cutoff,
        )
        .group_by(EventCheckin.user_id),
    )
    rows = result.all()
    count = len(rows)
    user_ids = [str(r[0]) for r in rows] if user.is_premium else []
    return {
        "event_id": str(event_id),
        "present_count": count,
        "user_ids": user_ids,
    }


__all__ = [
    "EVENT_CATEGORY_TO_TAGS",
    "list_events",
    "get_event_detail",
    "register_to_event",
    "unregister_from_event",
    "matches_preview",
    "get_event_stats",
    "checkin_event",
    "self_checkin_event",
    "list_present",
]
