from __future__ import annotations

"""
Match service (spec §5.7).

- list_matches       : matchs actifs (status="matched", non expirés)
- get_match_detail   : détail d'un match + ice-breaker + user complet
- unmatch            : soft (status="unmatched"), idempotent
"""

from datetime import datetime, timezone
from uuid import UUID

import structlog
from fastapi import status
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppException
from app.models.match import Match
from app.models.message import Message
from app.models.user import User
from app.models.user_quartier import UserQuartier
from app.models.user_spot import UserSpot
from app.services.feed_service import _hydrate_profile, _load_user_full
from app.services.icebreaker_service import generate_icebreaker

log = structlog.get_logger()


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════


def _partner_id(match: Match, me_id: UUID) -> UUID:
    return match.user_b_id if match.user_a_id == me_id else match.user_a_id


async def _fetch_last_message(
    match_id: UUID, db: AsyncSession
) -> Message | None:
    row = await db.execute(
        select(Message)
        .where(Message.match_id == match_id)
        .order_by(desc(Message.created_at))
        .limit(1)
    )
    return row.scalar_one_or_none()


async def _unread_count(
    match_id: UUID, me_id: UUID, db: AsyncSession
) -> int:
    from sqlalchemy import func

    row = await db.execute(
        select(func.count(Message.id)).where(
            Message.match_id == match_id,
            Message.sender_id != me_id,
            Message.status != "read",
        )
    )
    return int(row.scalar_one() or 0)


# ══════════════════════════════════════════════════════════════════════
# GET /matches
# ══════════════════════════════════════════════════════════════════════


async def list_matches(user: User, db: AsyncSession) -> dict:
    now = datetime.now(timezone.utc)
    rows = await db.execute(
        select(Match)
        .where(
            or_(Match.user_a_id == user.id, Match.user_b_id == user.id),
            Match.status == "matched",
            or_(Match.expires_at.is_(None), Match.expires_at > now),
        )
        .order_by(desc(Match.matched_at))
    )
    matches = rows.scalars().all()

    summaries: list[dict] = []
    for m in matches:
        partner_id = _partner_id(m, user.id)
        partner = await db.get(
            User,
            partner_id,
            options=[selectinload(User.profile), selectinload(User.photos)],
        )
        if partner is None or partner.profile is None:
            continue
        last_msg = await _fetch_last_message(m.id, db)
        unread = await _unread_count(m.id, user.id, db)

        photo_url = None
        for p in sorted(
            partner.photos or [], key=lambda ph: ph.display_order
        ):
            if p.moderation_status != "rejected":
                photo_url = p.medium_url
                break

        from datetime import date as _date

        today = _date.today()
        age = (
            today.year
            - partner.profile.birth_date.year
            - (
                (today.month, today.day)
                < (partner.profile.birth_date.month, partner.profile.birth_date.day)
            )
        )

        summaries.append(
            {
                "match_id": m.id,
                "user": {
                    "user_id": partner.id,
                    "display_name": partner.profile.display_name,
                    "age": age,
                    "photo_url": photo_url,
                    "is_verified": bool(partner.is_selfie_verified),
                    "last_active_at": partner.last_active_at,
                },
                "matched_at": m.matched_at or m.created_at,
                "last_message": (
                    {
                        "id": last_msg.id,
                        "sender_id": last_msg.sender_id,
                        "content": last_msg.content,
                        "message_type": last_msg.message_type,
                        "created_at": last_msg.created_at,
                    }
                    if last_msg
                    else None
                ),
                "unread_count": unread,
                "ice_breaker": None,
            }
        )

    return {"matches": summaries}


# ══════════════════════════════════════════════════════════════════════
# GET /matches/{id}
# ══════════════════════════════════════════════════════════════════════


async def get_match_detail(
    user: User, match_id: UUID, db: AsyncSession
) -> dict:
    match = await db.get(Match, match_id)
    if match is None or match.status in ("unmatched",):
        raise AppException(status.HTTP_404_NOT_FOUND, "match_not_found")
    if user.id not in (match.user_a_id, match.user_b_id):
        raise AppException(status.HTTP_404_NOT_FOUND, "match_not_found")

    partner_id = _partner_id(match, user.id)
    me_full = await _load_user_full(user.id, db)
    partner = await _load_user_full(partner_id, db)
    if partner is None or partner.profile is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "partner_not_available")

    user_dict = _hydrate_profile(
        me_full or user,
        partner,
        is_wildcard=bool(match.was_wildcard),
        is_new_user=False,
    )

    # (Re)génère l'ice-breaker à la demande — non persisté au MVP
    liker = await _load_user_full(match.user_a_id, db)
    recipient = await _load_user_full(match.user_b_id, db)
    ice_breaker = ""
    if liker and recipient:
        ice_breaker = await generate_icebreaker(match, liker, recipient, db)

    return {
        "match_id": match.id,
        "status": match.status,
        "user": user_dict,
        "matched_at": match.matched_at,
        "expires_at": match.expires_at,
        "ice_breaker": ice_breaker,
    }


# ══════════════════════════════════════════════════════════════════════
# DELETE /matches/{id}
# ══════════════════════════════════════════════════════════════════════


async def unmatch(user: User, match_id: UUID, db: AsyncSession) -> dict:
    match = await db.get(Match, match_id)
    if match is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "match_not_found")
    if user.id not in (match.user_a_id, match.user_b_id):
        raise AppException(status.HTTP_404_NOT_FOUND, "match_not_found")

    if match.status != "unmatched":
        match.status = "unmatched"
        match.unmatched_at = datetime.now(timezone.utc)
        match.unmatched_by = user.id
        await db.commit()
    return {"match_id": match.id, "status": "unmatched"}


__all__ = ["list_matches", "get_match_detail", "unmatch"]
