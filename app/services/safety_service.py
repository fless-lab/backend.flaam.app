from __future__ import annotations

"""
Safety service (spec §5.11, §18, §30).

Expose :
- report_user()           : crée Report + appelle scam_detection
- block_user()             : Block bidirectionnel + update AccountHistory
- unblock_user()           : retire le Block (ne désarme PAS les blocked_by_hashes)
- share_date()             : SMS (ou WhatsApp) via sms_service
- start_emergency_timer()  : Redis key TTL = timer_hours * 3600
- cancel_emergency_timer() : DEL de la key

Le timer d'urgence n'envoie PAS de SMS en Session 9 : on stocke l'état
Redis + on logge. L'envoi différé (quand la key expire) viendra en
Session 11 avec Celery beat qui pollera les timers expirés.
"""

import json
from datetime import datetime, timezone
from uuid import UUID

import redis.asyncio as aioredis
import structlog
from fastapi import status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.account_history import AccountHistory
from app.models.block import Block
from app.models.report import Report
from app.models.user import User
from app.services import scam_detection_service
from app.utils.sms import sms_service

log = structlog.get_logger()


# ── Redis keys ────────────────────────────────────────────────────────

TIMER_KEY = "safety:timer:{user_id}"


# ══════════════════════════════════════════════════════════════════════
# Report
# ══════════════════════════════════════════════════════════════════════


async def report_user(
    *,
    reporter: User,
    reported_user_id: UUID,
    reason: str,
    description: str | None,
    evidence_message_ids: list[UUID] | None,
    db: AsyncSession,
) -> Report:
    """Crée un Report + déclenche scam_detection sur le reported (synchrone)."""
    if reported_user_id == reporter.id:
        raise AppException(
            status.HTTP_400_BAD_REQUEST, "cannot_report_self"
        )

    reported = await db.get(User, reported_user_id)
    if reported is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "user_not_found")

    report = Report(
        reporter_id=reporter.id,
        reported_user_id=reported_user_id,
        reason=reason,
        description=description,
        evidence_message_ids=(
            [str(mid) for mid in evidence_message_ids]
            if evidence_message_ids
            else None
        ),
        status="pending",
    )
    db.add(report)
    await db.flush()

    # Scam detection synchrone — appelée à chaque report reçu (§39).
    risk = await scam_detection_service.compute_scam_risk(
        reported_user_id, db
    )
    if risk > scam_detection_service.AUTO_BAN_THRESHOLD:
        reported.is_banned = True
        reported.ban_reason = f"auto_ban_scam_risk:{risk:.2f}"
        report.status = "auto_banned"
        log.warning(
            "scam_auto_ban",
            user_id=str(reported_user_id),
            risk=risk,
        )
    elif risk > scam_detection_service.REVIEW_THRESHOLD:
        report.status = "flagged_for_review"

    await db.commit()
    await db.refresh(report)
    return report


# ══════════════════════════════════════════════════════════════════════
# Block / Unblock
# ══════════════════════════════════════════════════════════════════════


async def block_user(
    *,
    blocker: User,
    blocked_user_id: UUID,
    db: AsyncSession,
) -> Block:
    """
    Crée un Block (idempotent) + met à jour l'AccountHistory du bloqué.

    Le filtre matching exclut déjà les blocks bidirectionnels
    (app/services/matching_engine/hard_filters.py). L'effet est donc
    immédiat au prochain calcul de feed.
    """
    if blocked_user_id == blocker.id:
        raise AppException(
            status.HTTP_400_BAD_REQUEST, "cannot_block_self"
        )

    blocked = await db.get(User, blocked_user_id)
    if blocked is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "user_not_found")

    # Idempotent
    existing = await db.execute(
        select(Block).where(
            Block.blocker_id == blocker.id,
            Block.blocked_id == blocked_user_id,
        )
    )
    block = existing.scalar_one_or_none()
    if block is None:
        block = Block(blocker_id=blocker.id, blocked_id=blocked_user_id)
        db.add(block)

    # AccountHistory du bloqué (§30.2) : blocked_by_count + blocked_by_hashes.
    history_row = await db.execute(
        select(AccountHistory).where(
            AccountHistory.phone_hash == blocked.phone_hash
        )
    )
    history = history_row.scalar_one_or_none()
    if history is None:
        history = AccountHistory(
            phone_hash=blocked.phone_hash,
            device_fingerprints=[],
            total_accounts_created=blocked.account_created_count or 1,
            first_account_created_at=blocked.created_at,
            blocked_by_hashes=[blocker.phone_hash],
            blocked_by_count=1,
        )
        db.add(history)
    else:
        # blocked_by_hashes : liste des phone_hash qui ont bloqué ce user
        if blocker.phone_hash not in (history.blocked_by_hashes or []):
            history.blocked_by_hashes = [
                *(history.blocked_by_hashes or []),
                blocker.phone_hash,
            ]
            history.blocked_by_count = (history.blocked_by_count or 0) + 1

    await db.commit()
    await db.refresh(block)
    return block


async def unblock_user(
    *,
    blocker: User,
    blocked_user_id: UUID,
    db: AsyncSession,
) -> bool:
    """
    Retire le Block. Ne décrémente PAS blocked_by_count de l'historique :
    la spec §30.7 conserve les blocks survivants à la suppression, on
    garde la même logique ici (un unblock peut être temporaire, le
    compteur historique garde la trace).
    """
    result = await db.execute(
        select(Block).where(
            Block.blocker_id == blocker.id,
            Block.blocked_id == blocked_user_id,
        )
    )
    block = result.scalar_one_or_none()
    if block is None:
        return False
    await db.delete(block)
    await db.commit()
    return True


# ══════════════════════════════════════════════════════════════════════
# Share date (SMS/WhatsApp au contact de confiance)
# ══════════════════════════════════════════════════════════════════════


def _format_share_date_message(
    *,
    user_name: str,
    partner_name: str,
    meeting_place: str,
    meeting_time: datetime,
) -> str:
    when = meeting_time.strftime("%d/%m/%Y à %Hh%M")
    return (
        f"[Flaam] {user_name} a un rendez-vous avec {partner_name} "
        f"le {when} à {meeting_place}. "
        f"Message automatique de sécurité."
    )


async def share_date(
    *,
    user: User,
    contact_phone: str,
    contact_name: str | None,
    partner_name: str,
    meeting_place: str,
    meeting_time: datetime,
) -> dict:
    display_name = (
        user.first_name
        or (user.profile.display_name if user.profile else None)
        or "Ton contact Flaam"
    )
    text = _format_share_date_message(
        user_name=display_name,
        partner_name=partner_name,
        meeting_place=meeting_place,
        meeting_time=meeting_time,
    )
    # WhatsApp primary pour les messages libres (§35 — 4x moins cher).
    result = await sms_service.send_text(
        contact_phone, text, channel="whatsapp"
    )
    log.info(
        "share_date_sent",
        user_id=str(user.id),
        contact_name=contact_name,
        provider=result.get("provider"),
    )
    return result


# ══════════════════════════════════════════════════════════════════════
# Emergency timer
# ══════════════════════════════════════════════════════════════════════


async def start_emergency_timer(
    *,
    user: User,
    contact_phone: str,
    contact_name: str | None,
    timer_hours: int,
    latitude: float | None,
    longitude: float | None,
    meeting_place: str | None,
    redis: aioredis.Redis,
) -> datetime:
    """
    Stocke l'état du timer en Redis avec TTL = timer_hours * 3600.
    Si la clé expire sans cancel → Celery beat (Session 11) détecte
    et envoie le SMS d'alerte au contact.
    """
    ttl_seconds = timer_hours * 3600
    now = datetime.now(timezone.utc)
    expires_at = datetime.fromtimestamp(
        now.timestamp() + ttl_seconds, tz=timezone.utc
    )
    payload = {
        "user_id": str(user.id),
        "contact_phone": contact_phone,
        "contact_name": contact_name,
        "started_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "latitude": latitude,
        "longitude": longitude,
        "meeting_place": meeting_place,
    }
    await redis.set(
        TIMER_KEY.format(user_id=str(user.id)),
        json.dumps(payload),
        ex=ttl_seconds,
    )
    log.info(
        "emergency_timer_armed",
        user_id=str(user.id),
        expires_at=expires_at.isoformat(),
        hours=timer_hours,
    )
    return expires_at


async def cancel_emergency_timer(
    *, user: User, redis: aioredis.Redis
) -> bool:
    deleted = await redis.delete(TIMER_KEY.format(user_id=str(user.id)))
    cancelled = bool(deleted)
    log.info(
        "emergency_timer_cancel",
        user_id=str(user.id),
        cancelled=cancelled,
    )
    return cancelled


__all__ = [
    "report_user",
    "block_user",
    "unblock_user",
    "share_date",
    "start_emergency_timer",
    "cancel_emergency_timer",
]
