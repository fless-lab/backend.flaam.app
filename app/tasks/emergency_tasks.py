from __future__ import annotations

"""
Emergency SMS task (§S12, safety §5.11).

send_emergency_sms : toutes les minutes.
- SCAN safety:timer:* dans Redis.
- Pour chaque clé : lit le JSON, compare expires_at_utc à now.
- Si expiré → envoie un SMS d'alerte au contact de confiance, supprime
  la clé.
- Sinon : laisse la clé (le user peut encore cancel, ou le timer n'est
  pas encore atteint).

Le TTL Redis côté safety_service inclut une grâce de 24h pour que le
task (qui tourne toutes les minutes) puisse attraper les timers
logiquement expirés avant disparition physique de la clé.
"""

import asyncio
import json
from datetime import datetime, timezone

import redis.asyncio as aioredis
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.db.redis import redis_pool
from app.db.session import async_session
from app.utils.sms import sms_service

log = structlog.get_logger()


_SCAN_MATCH = "safety:timer:*"
_SCAN_BATCH = 50


def _format_alert_sms(data: dict) -> str:
    user_name = data.get("user_name") or "Ton contact Flaam"
    meeting_place = data.get("meeting_place") or "un lieu non précisé"
    return (
        f"[Flaam] ALERTE : {user_name} avait un rendez-vous a "
        f"{meeting_place} et n'a pas annule son timer de securite. "
        f"Verifie que tout va bien."
    )


def _mask_phone(phone: str) -> str:
    """N'expose que les 4 derniers chiffres dans les logs."""
    if not phone or len(phone) < 4:
        return "****"
    return f"****{phone[-4:]}"


async def _send_emergency_sms_async(
    db: AsyncSession,  # accepté pour symétrie, pas utilisé
    redis: aioredis.Redis,
    *,
    now: datetime | None = None,
) -> dict:
    """
    Scan Redis, envoie SMS pour les timers expirés.

    Retourne : {"scanned": int, "sent": int, "errors": int}.
    """
    now_ = now or datetime.now(timezone.utc)
    cursor = 0
    scanned = 0
    sent = 0
    errors = 0

    while True:
        cursor, keys = await redis.scan(
            cursor, match=_SCAN_MATCH, count=_SCAN_BATCH
        )
        for key in keys:
            scanned += 1
            raw = await redis.get(key)
            if raw is None:
                continue
            try:
                data = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                log.warning("emergency_timer_corrupted", key=str(key))
                await redis.delete(key)
                errors += 1
                continue

            exp_raw = data.get("expires_at_utc") or data.get("expires_at")
            if not exp_raw:
                log.warning("emergency_timer_no_expiry", key=str(key))
                await redis.delete(key)
                errors += 1
                continue

            try:
                expires_at = datetime.fromisoformat(exp_raw)
            except ValueError:
                log.warning("emergency_timer_bad_expiry", key=str(key))
                await redis.delete(key)
                errors += 1
                continue

            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at > now_:
                continue  # timer encore actif

            contact_phone = data.get("contact_phone")
            if not contact_phone:
                log.warning("emergency_timer_no_contact", key=str(key))
                await redis.delete(key)
                errors += 1
                continue

            text = _format_alert_sms(data)
            try:
                await sms_service.send_text(
                    contact_phone, text, channel="whatsapp"
                )
                sent += 1
                log.warning(
                    "emergency_sms_sent",
                    user_id=data.get("user_id"),
                    contact=_mask_phone(contact_phone),
                )
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "emergency_sms_failed",
                    user_id=data.get("user_id"),
                    error=str(exc),
                )
                errors += 1
                # On NE supprime PAS la clé : retry à la prochaine tick.
                continue

            await redis.delete(key)

        if cursor == 0:
            break

    return {"scanned": scanned, "sent": sent, "errors": errors}


@celery_app.task(name="app.tasks.emergency_tasks.send_emergency_sms")
def send_emergency_sms() -> dict:
    async def _run():
        async with async_session() as db:
            return await _send_emergency_sms_async(db, redis_pool.client)

    return asyncio.run(_run())


__all__ = [
    "_send_emergency_sms_async",
    "send_emergency_sms",
]
