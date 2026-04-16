from __future__ import annotations

"""
Event boost — MàJ 8 Porte 3 §5.

Les participants d'un même event récent (checked_in ou converted)
reçoivent un boost temporaire sur leur score géo (L2) dans le feed
de l'autre.

- Boost : +15 points (sur une échelle 0-100).
- Durée totale : 14 jours.
- Plateau : 0-7 jours → boost complet.
- Decay linéaire : 7-14 jours → décroît à zéro.
- Conditions : les DEUX participants doivent avoir un status
  ∈ {checked_in, converted} sur l'EventRegistration.

Le boost est un MULTIPLICATEUR POST-L2 uniquement — il n'affecte pas
L1 (hard filters). Si un candidat est filtré par L1 (genre incompatible,
bloqué, etc.), l'event boost ne le fait PAS réapparaître.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event
from app.models.event_registration import EventRegistration


# Config. Les valeurs pourraient devenir dynamiques via matching_config
# en Session 10 (admin API). Au MVP : constantes.
EVENT_BOOST_CONFIG: dict[str, float | int] = {
    "boost_points": 15,         # Points ajoutés au score L2 (0-100)
    "full_boost_days": 7,       # Plateau complet
    "decay_days": 7,            # Fenêtre de decay
    "total_duration_days": 14,  # Après → 0
}


def _days_since(ended_at: datetime, now: datetime) -> int:
    if ended_at.tzinfo is None:
        ended_at = ended_at.replace(tzinfo=timezone.utc)
    return max(0, (now - ended_at).days)


def _boost_value(days_since: int) -> float:
    full = int(EVENT_BOOST_CONFIG["full_boost_days"])
    total = int(EVENT_BOOST_CONFIG["total_duration_days"])
    decay_window = int(EVENT_BOOST_CONFIG["decay_days"])
    points = float(EVENT_BOOST_CONFIG["boost_points"])

    if days_since <= full:
        return points
    if days_since <= total:
        remaining = total - days_since
        return points * (remaining / decay_window)
    return 0.0


async def compute_event_boosts(
    user_id: UUID,
    candidate_ids: list[UUID],
    db_session: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict[UUID, float]:
    """
    Retourne {candidate_id: boost_points} pour les candidats qui étaient
    au même event récent que `user_id` (both checked_in/converted).

    Score = max boost sur tous les events communs dans la fenêtre.
    """
    if not candidate_ids:
        return {}

    now_utc = now or datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(
        days=int(EVENT_BOOST_CONFIG["total_duration_days"])
    )

    # Events récents où `user_id` a été checked_in ou converted.
    # On joint Event pour utiliser ends_at (ou starts_at en fallback).
    my_events_rows = await db_session.execute(
        select(EventRegistration.event_id, Event.ends_at, Event.starts_at)
        .join(Event, Event.id == EventRegistration.event_id)
        .where(
            EventRegistration.user_id == user_id,
            EventRegistration.status.in_(("checked_in", "converted")),
            Event.starts_at >= cutoff,
        )
    )
    my_events: dict[UUID, datetime] = {}
    for ev_id, ends_at, starts_at in my_events_rows.all():
        ref = ends_at or starts_at
        my_events[ev_id] = ref

    if not my_events:
        return {}

    # Co-participants checked_in ou converted dans mes events
    co_rows = await db_session.execute(
        select(EventRegistration.user_id, EventRegistration.event_id)
        .where(
            EventRegistration.event_id.in_(list(my_events.keys())),
            EventRegistration.user_id.in_(candidate_ids),
            EventRegistration.user_id != user_id,
            EventRegistration.status.in_(("checked_in", "converted")),
        )
    )

    boosts: dict[UUID, float] = {}
    for cid, ev_id in co_rows.all():
        ended = my_events.get(ev_id)
        if ended is None:
            continue
        days = _days_since(ended, now_utc)
        boost = _boost_value(days)
        if boost > boosts.get(cid, 0.0):
            boosts[cid] = boost

    return boosts


async def find_shared_recent_event(
    user_a_id: UUID,
    user_b_id: UUID,
    db_session: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict | None:
    """
    Trouve l'event récent (<14j) partagé par deux users (both checked_in
    ou converted). Utilisé par icebreaker_service pour le niveau 0
    "same_event".

    Retourne {event_id, event_name, days_ago} ou None.
    """
    now_utc = now or datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(
        days=int(EVENT_BOOST_CONFIG["total_duration_days"])
    )

    # Intersection des events récents des deux users
    a_rows = await db_session.execute(
        select(EventRegistration.event_id)
        .join(Event, Event.id == EventRegistration.event_id)
        .where(
            EventRegistration.user_id == user_a_id,
            EventRegistration.status.in_(("checked_in", "converted")),
            Event.starts_at >= cutoff,
        )
    )
    a_events = {r[0] for r in a_rows.all()}
    if not a_events:
        return None

    b_rows = await db_session.execute(
        select(EventRegistration.event_id)
        .join(Event, Event.id == EventRegistration.event_id)
        .where(
            EventRegistration.user_id == user_b_id,
            EventRegistration.event_id.in_(a_events),
            EventRegistration.status.in_(("checked_in", "converted")),
        )
    )
    shared = {r[0] for r in b_rows.all()}
    if not shared:
        return None

    # Charge les détails des events partagés, prend le plus récent
    ev_rows = await db_session.execute(
        select(Event.id, Event.title, Event.ends_at, Event.starts_at)
        .where(Event.id.in_(shared))
        .order_by(Event.starts_at.desc())
    )
    best: tuple[UUID, str, int] | None = None
    for ev_id, title, ends_at, starts_at in ev_rows.all():
        ref = ends_at or starts_at
        days = _days_since(ref, now_utc)
        if best is None or days < best[2]:
            best = (ev_id, title, days)

    if best is None:
        return None
    return {"event_id": best[0], "event_name": best[1], "days_ago": best[2]}


__all__ = [
    "EVENT_BOOST_CONFIG",
    "compute_event_boosts",
    "find_shared_recent_event",
]
