from __future__ import annotations

"""
Waitlist service (MàJ 7).

- `process_waitlist_join` : femme → status "activated" immédiat,
  homme/autre → status "waiting" avec position = last+1.
- `release_batch` : libère N hommes si le ratio femmes > 40 %.
- `get_waitlist_position` : position + total en attente.

Le mapping Profile.gender → WaitlistEntry.gender :
  "woman" → "female", "man" → "male", "non_binary" → "other".
"""

from datetime import datetime, timezone
from uuid import UUID

import structlog
from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.city import City
from app.models.user import User
from app.models.waitlist_entry import WaitlistEntry

log = structlog.get_logger()


WAITLIST_BATCH_SIZE = 50
MIN_FEMALE_RATIO_FOR_RELEASE = 0.40


def map_profile_gender_to_waitlist(profile_gender: str | None) -> str:
    if profile_gender == "woman":
        return "female"
    if profile_gender == "man":
        return "male"
    return "other"


def _derive_gender(user: User, fallback: str | None) -> str:
    if fallback is not None:
        return fallback
    if user.profile is not None and user.profile.gender:
        return map_profile_gender_to_waitlist(user.profile.gender)
    return "other"


# ── Process join ─────────────────────────────────────────────────────

async def process_waitlist_join(
    user: User,
    city_id: UUID,
    db: AsyncSession,
    *,
    gender: str | None = None,
    invite_code_used: str | None = None,
) -> dict:
    """
    Place l'utilisateur dans la waitlist d'une ville. Idempotent :
    si une entrée existe déjà, on la retourne inchangée.
    """
    city = await db.get(City, city_id)
    if city is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "city_not_found")

    existing = await db.execute(
        select(WaitlistEntry).where(WaitlistEntry.user_id == user.id)
    )
    entry = existing.scalar_one_or_none()
    if entry is not None:
        return _as_response(entry, city_id=city_id, db=None)

    waitlist_gender = _derive_gender(user, gender)

    # Femme OU invite_code valide → accès immédiat
    if waitlist_gender == "female" or invite_code_used is not None:
        entry = WaitlistEntry(
            city_id=city_id,
            user_id=user.id,
            gender=waitlist_gender,
            position=0,
            status="activated",
            activated_at=datetime.now(timezone.utc),
            invite_code_used=invite_code_used,
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        log.info(
            "waitlist_activated",
            user_id=str(user.id),
            city_id=str(city_id),
            gender=waitlist_gender,
            via_invite=invite_code_used is not None,
        )
        return {
            "status": "activated",
            "position": 0,
            "total_waiting": None,
            "message": (
                "Bienvenue sur Flaam !" if invite_code_used is None
                else "Code valide, bienvenue sur Flaam !"
            ),
        }

    # Hommes / autres → position de file.
    # On compte AVANT d'ajouter l'entry : "il y avait N en attente,
    # je suis le N+1e". Évite les pièges auto-flush selon la session.
    current_waiting = await db.scalar(
        select(func.count(WaitlistEntry.id)).where(
            WaitlistEntry.city_id == city_id,
            WaitlistEntry.status == "waiting",
        )
    ) or 0

    entry = WaitlistEntry(
        city_id=city_id,
        user_id=user.id,
        gender=waitlist_gender,
        position=current_waiting + 1,
        status="waiting",
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    log.info(
        "waitlist_waiting",
        user_id=str(user.id),
        city_id=str(city_id),
        position=entry.position,
    )
    return {
        "status": "waiting",
        "position": entry.position,
        "total_waiting": entry.position,
        "message": f"Tu es #{entry.position} sur la liste d'attente.",
    }


# ── Release batch ────────────────────────────────────────────────────

async def release_batch(
    city_id: UUID, db: AsyncSession, *, size: int = WAITLIST_BATCH_SIZE
) -> dict:
    """
    Libère jusqu'à `size` hommes de la waitlist si le ratio femmes
    dépasse `MIN_FEMALE_RATIO_FOR_RELEASE`. Retourne un résumé.
    """
    # Compteurs actifs (status activated/invited)
    active_total = await db.scalar(
        select(func.count(WaitlistEntry.id)).where(
            WaitlistEntry.city_id == city_id,
            WaitlistEntry.status.in_(("activated", "invited")),
        )
    ) or 0
    active_female = await db.scalar(
        select(func.count(WaitlistEntry.id)).where(
            WaitlistEntry.city_id == city_id,
            WaitlistEntry.status.in_(("activated", "invited")),
            WaitlistEntry.gender == "female",
        )
    ) or 0
    ratio = (active_female / active_total) if active_total else 0.0

    if ratio < MIN_FEMALE_RATIO_FOR_RELEASE:
        return {
            "released": 0,
            "reason": "female_ratio_too_low",
            "ratio": round(ratio, 4),
            "min_required": MIN_FEMALE_RATIO_FOR_RELEASE,
        }

    result = await db.execute(
        select(WaitlistEntry)
        .where(
            WaitlistEntry.city_id == city_id,
            WaitlistEntry.status == "waiting",
        )
        .order_by(WaitlistEntry.position)
        .limit(size)
    )
    to_release = list(result.scalars().all())

    now = datetime.now(timezone.utc)
    for entry in to_release:
        entry.status = "invited"
        entry.invited_at = now

    await db.commit()
    log.info(
        "waitlist_release_batch",
        city_id=str(city_id),
        released=len(to_release),
        ratio=ratio,
    )
    return {
        "released": len(to_release),
        "ratio": round(ratio, 4),
        "user_ids": [str(e.user_id) for e in to_release],
    }


# ── Position ─────────────────────────────────────────────────────────

async def get_waitlist_position(
    user: User, db: AsyncSession
) -> dict | None:
    result = await db.execute(
        select(WaitlistEntry).where(WaitlistEntry.user_id == user.id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        return None

    total = await db.scalar(
        select(func.count(WaitlistEntry.id)).where(
            WaitlistEntry.city_id == entry.city_id,
            WaitlistEntry.status == "waiting",
        )
    ) or 0
    return {
        "status": entry.status,
        "position": entry.position,
        "total_waiting": total,
        "city_id": entry.city_id,
    }


# ── Helpers ──────────────────────────────────────────────────────────

def _as_response(entry: WaitlistEntry, *, city_id: UUID, db: None) -> dict:
    """Formate une WaitlistEntry existante en réponse idempotente."""
    if entry.status == "activated":
        msg = "Bienvenue sur Flaam !"
    elif entry.status == "invited":
        msg = "Tu peux rejoindre Flaam dès maintenant."
    else:
        msg = f"Tu es #{entry.position} sur la liste d'attente."
    return {
        "status": entry.status,
        "position": entry.position,
        "total_waiting": None,
        "message": msg,
    }


__all__ = [
    "WAITLIST_BATCH_SIZE",
    "MIN_FEMALE_RATIO_FOR_RELEASE",
    "map_profile_gender_to_waitlist",
    "process_waitlist_join",
    "release_batch",
    "get_waitlist_position",
]
