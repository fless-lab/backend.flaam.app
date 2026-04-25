from __future__ import annotations

"""
Seen IRL service (S2) — "Tu as croisé X à Y".

Usage :
- GET /matches/seen-irl : liste des users que l'appelant a croisés à un
  event récent (≤3 jours) sans qu'ils soient déjà en Match.
- Celery task `send_seen_irl_pushes` (J+1 après check-in) qui envoie un
  push doux pour relancer la connexion IRL.

Sentiment cible : "Hier soir tu étais à AfroBeats Night avec elle. Si tu
hésitais, voilà un rappel calme." Pas de pression, pas d'urgence.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import structlog
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.event import Event
from app.models.event_checkin import EventCheckin
from app.models.match import Match
from app.models.user import User
from app.models.block import Block

log = structlog.get_logger()


# Fenêtre de visibilité du "seen IRL" : on remonte les check-ins jusqu'à
# 3 jours en arrière. Au-delà l'effet IRL s'éteint.
SEEN_IRL_WINDOW_DAYS = 3


async def list_seen_irl(
    user: User, db: AsyncSession,
) -> list[dict]:
    """
    Liste les users que l'appelant a croisés à un event ≤3j sans être
    déjà en Match avec eux. Ordre chronologique (event le + récent d'abord).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=SEEN_IRL_WINDOW_DAYS)

    # 1. Events récents auxquels j'ai checkin verifié
    my_checkins = await db.execute(
        select(EventCheckin.event_id, EventCheckin.at)
        .where(
            EventCheckin.user_id == user.id,
            EventCheckin.verified.is_(True),
            EventCheckin.at >= cutoff,
        )
        .order_by(EventCheckin.at.desc()),
    )
    my_event_ids: list[tuple[UUID, datetime]] = [
        (row[0], row[1]) for row in my_checkins.all()
    ]
    if not my_event_ids:
        return []

    event_ids = [eid for eid, _ in my_event_ids]

    # 2. Autres users qui ont check-in à ces events
    others = await db.execute(
        select(EventCheckin.event_id, EventCheckin.user_id, EventCheckin.at)
        .where(
            EventCheckin.event_id.in_(event_ids),
            EventCheckin.verified.is_(True),
            EventCheckin.user_id != user.id,
        ),
    )
    # On dédoublonne en gardant le check-in le + récent par (other_user, event)
    by_pair: dict[tuple[UUID, UUID], datetime] = {}
    for eid, uid, at in others.all():
        key = (uid, eid)
        prev = by_pair.get(key)
        if prev is None or at > prev:
            by_pair[key] = at

    other_user_ids = {uid for uid, _ in by_pair.keys()}
    if not other_user_ids:
        return []

    # 3. Filtrer les users déjà en Match (peu importe le statut) avec moi
    match_rows = await db.execute(
        select(Match.user_a_id, Match.user_b_id).where(
            or_(
                and_(
                    Match.user_a_id == user.id,
                    Match.user_b_id.in_(other_user_ids),
                ),
                and_(
                    Match.user_b_id == user.id,
                    Match.user_a_id.in_(other_user_ids),
                ),
            ),
        ),
    )
    matched_with: set[UUID] = set()
    for a, b in match_rows.all():
        matched_with.add(b if a == user.id else a)

    # 4. Filtrer les users bloqués (dans les 2 sens)
    block_rows = await db.execute(
        select(Block.blocker_id, Block.blocked_id).where(
            or_(
                and_(
                    Block.blocker_id == user.id,
                    Block.blocked_id.in_(other_user_ids),
                ),
                and_(
                    Block.blocked_id == user.id,
                    Block.blocker_id.in_(other_user_ids),
                ),
            ),
        ),
    )
    blocked_set: set[UUID] = set()
    for blocker, blocked in block_rows.all():
        blocked_set.add(blocked if blocker == user.id else blocker)

    candidate_ids = other_user_ids - matched_with - blocked_set
    if not candidate_ids:
        return []

    # 5. Charger users + profiles + premier photo + event title
    users_rows = await db.execute(
        select(User)
        .options(
            selectinload(User.profile),
            selectinload(User.photos),
        )
        .where(
            User.id.in_(candidate_ids),
            User.is_deleted.is_(False),
            User.is_banned.is_(False),
        ),
    )
    users_by_id: dict[UUID, User] = {u.id: u for u in users_rows.scalars()}

    events_rows = await db.execute(
        select(Event).where(Event.id.in_(event_ids)),
    )
    events_by_id: dict[UUID, Event] = {e.id: e for e in events_rows.scalars()}

    items: list[dict] = []
    # Itérer chronologiquement (event le + récent d'abord)
    for (uid, eid), at in sorted(by_pair.items(), key=lambda kv: kv[1], reverse=True):
        if uid not in candidate_ids:
            continue
        other = users_by_id.get(uid)
        ev = events_by_id.get(eid)
        if other is None or other.profile is None or ev is None:
            continue
        photo_url = _first_photo_url(other)
        items.append(
            {
                "user_id": other.id,
                "display_name": other.profile.display_name,
                "age": _age(other.profile.birth_date),
                "photo_url": photo_url,
                "is_verified": bool(other.is_selfie_verified),
                "event_id": ev.id,
                "event_title": ev.title,
                "event_at": at,
            },
        )
    return items


def _first_photo_url(user: User) -> str | None:
    """Renvoie l'URL medium de la première photo visible du user."""
    photos = sorted(
        (p for p in (user.photos or []) if not p.is_deleted),
        key=lambda p: p.display_order,
    )
    if not photos:
        return None
    return photos[0].medium_url


def _age(birth) -> int:
    today = datetime.now(timezone.utc).date()
    return today.year - birth.year - (
        (today.month, today.day) < (birth.month, birth.day)
    )


# ── Push J+1 ────────────────────────────────────────────────────────


async def get_yesterday_pairs(
    db: AsyncSession,
) -> list[tuple[UUID, UUID, UUID, str]]:
    """
    Pour chaque user qui a check-in verifié hier, calcule les paires
    (target_user, other_user, event_id, event_title) à notifier.

    On limite par target_user à max 1 paire (la plus pertinente — premier
    other rencontré) pour éviter le spam : un seul push J+1 par user.
    """
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    end = start + timedelta(days=1)

    rows = await db.execute(
        select(EventCheckin.event_id, EventCheckin.user_id, EventCheckin.at)
        .where(
            EventCheckin.verified.is_(True),
            EventCheckin.at >= start,
            EventCheckin.at < end,
        ),
    )
    by_event: dict[UUID, list[tuple[UUID, datetime]]] = {}
    for eid, uid, at in rows.all():
        by_event.setdefault(eid, []).append((uid, at))

    if not by_event:
        return []

    event_ids = list(by_event.keys())
    events_rows = await db.execute(
        select(Event).where(Event.id.in_(event_ids)),
    )
    events_by_id = {e.id: e for e in events_rows.scalars()}

    # Pour chaque event, générer les paires (chacun voit les autres)
    pairs: list[tuple[UUID, UUID, UUID, str]] = []
    notified: set[UUID] = set()  # un seul push par target_user
    for eid, attendees in by_event.items():
        ev = events_by_id.get(eid)
        if ev is None:
            continue
        # Trier par check-in time pour reproductibilité
        attendees.sort(key=lambda x: x[1])
        ids = [uid for uid, _ in attendees]
        if len(ids) < 2:
            continue
        for target in ids:
            if target in notified:
                continue
            for other in ids:
                if other == target:
                    continue
                # Skip si déjà en Match
                already = await db.execute(
                    select(Match.id).where(
                        or_(
                            and_(
                                Match.user_a_id == target,
                                Match.user_b_id == other,
                            ),
                            and_(
                                Match.user_b_id == target,
                                Match.user_a_id == other,
                            ),
                        ),
                    ).limit(1),
                )
                if already.scalar_one_or_none() is not None:
                    continue
                pairs.append((target, other, eid, ev.title))
                notified.add(target)
                break

    return pairs


__all__ = [
    "SEEN_IRL_WINDOW_DAYS",
    "list_seen_irl",
    "get_yesterday_pairs",
]
