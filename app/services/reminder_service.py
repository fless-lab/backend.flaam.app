from __future__ import annotations

"""
Reply reminders (Feature C, Session 9).

Détecte les conversations avec un message non-répondu depuis > 24h et
prépare une notification push "Tu n'as pas encore répondu à {name}.".

Garde-fous :
- Max 1 reminder par match / 48h (clé Redis `reminder:{match_id}`)
- Respecte notification_preferences.reply_reminders (feature-level)
- Respecte aussi le feature flag global `flag_reply_reminders_enabled`

Le scheduling effectif (Celery beat) viendra en Session 11.
Pour l'instant : check_pending_replies() est appelable par un job ou
un test. Il retourne la liste des matches à relancer.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import redis.asyncio as aioredis
import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match import Match
from app.models.message import Message
from app.models.notification_preference import NotificationPreference
from app.models.profile import Profile
from app.models.user import User
from app.services.config_service import get_config

log = structlog.get_logger()


REMINDER_COOLDOWN_KEY = "reminder:{match_id}"
REMINDER_COOLDOWN_SECONDS = 48 * 3600

UNREPLIED_THRESHOLD = timedelta(hours=24)


async def check_pending_replies(
    db: AsyncSession,
    redis: aioredis.Redis,
    *,
    now: datetime | None = None,
) -> list[dict]:
    """
    Retourne la liste des candidats reminder :
        [{"match_id", "recipient_id", "partner_name", "last_message_at"}, ...]

    Un candidat est un match `matched` où :
    1. Le dernier message (toutes directions) a > 24h
    2. Le dernier message a été envoyé par l'AUTRE user (attend une réponse)
    3. Pas de reminder envoyé dans les 48h (clé Redis)
    4. Le recipient a reply_reminders activé

    Respecte le flag global : si désactivé, retourne [].
    """
    # Feature flag global
    enabled = await get_config(
        "flag_reply_reminders_enabled", redis, db
    )
    if enabled < 0.5:
        return []

    now = now or datetime.now(timezone.utc)
    cutoff = now - UNREPLIED_THRESHOLD

    # Sous-requête : pour chaque match, timestamp du dernier message.
    last_msg = (
        select(
            Message.match_id.label("mid"),
            func.max(Message.created_at).label("last_at"),
        )
        .group_by(Message.match_id)
        .subquery()
    )

    # Match matched + dernier message avant cutoff
    rows = await db.execute(
        select(Match, last_msg.c.last_at)
        .join(last_msg, last_msg.c.mid == Match.id)
        .where(
            Match.status == "matched",
            last_msg.c.last_at <= cutoff,
        )
    )

    out: list[dict] = []
    for match, last_at in rows.all():
        # Qui a envoyé le dernier message ?
        last_sender_row = await db.execute(
            select(Message.sender_id)
            .where(Message.match_id == match.id)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        last_sender = last_sender_row.scalar_one_or_none()
        if last_sender is None:
            continue

        # Le recipient = l'autre user
        if last_sender == match.user_a_id:
            recipient_id = match.user_b_id
        elif last_sender == match.user_b_id:
            recipient_id = match.user_a_id
        else:
            continue

        # Cooldown 48h
        cd_key = REMINDER_COOLDOWN_KEY.format(match_id=str(match.id))
        if await redis.exists(cd_key):
            continue

        # Préférence notification du recipient
        pref_row = await db.execute(
            select(NotificationPreference.reply_reminders).where(
                NotificationPreference.user_id == recipient_id
            )
        )
        pref = pref_row.scalar_one_or_none()
        # Si la pref n'existe pas : on considère activé par défaut (le
        # champ server_default est True).
        if pref is False:
            continue

        # Partner (sender) pour le message
        partner_row = await db.execute(
            select(Profile.display_name).where(
                Profile.user_id == last_sender
            )
        )
        partner_name = partner_row.scalar_one_or_none() or "Ton contact"

        out.append(
            {
                "match_id": match.id,
                "recipient_id": recipient_id,
                "partner_name": partner_name,
                "last_message_at": last_at,
            }
        )

    return out


async def mark_reminder_sent(
    match_id: UUID, redis: aioredis.Redis
) -> None:
    """
    Pose le cooldown 48h. À appeler APRÈS que le Celery task a
    effectivement envoyé la notif push (Session 11).
    """
    await redis.set(
        REMINDER_COOLDOWN_KEY.format(match_id=str(match_id)),
        "1",
        ex=REMINDER_COOLDOWN_SECONDS,
    )


__all__ = [
    "check_pending_replies",
    "mark_reminder_sent",
    "UNREPLIED_THRESHOLD",
    "REMINDER_COOLDOWN_SECONDS",
]
