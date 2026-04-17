from __future__ import annotations

"""
Reply reminders task (Feature C, §S9 + câblage Celery §S12).

send_reply_reminders : toutes les 4h.
- Appelle reminder_service.check_pending_replies (respecte déjà le flag
  global, le cooldown 48h Redis, et reply_reminders pref).
- Envoie notif_reply_reminder pour chaque candidat.
- Marque le cooldown via mark_reminder_sent APRÈS un push réussi.
"""

import asyncio

import redis.asyncio as aioredis
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.db.redis import redis_pool
from app.db.session import async_session
from app.services import notification_service, reminder_service

log = structlog.get_logger()


async def _send_reply_reminders_async(
    db: AsyncSession, redis: aioredis.Redis
) -> dict:
    """
    Retourne : {"candidates": int, "sent": int}.
    """
    candidates = await reminder_service.check_pending_replies(db, redis)
    sent = 0
    for item in candidates:
        result = await notification_service.send_push(
            item["recipient_id"],
            type="notif_reply_reminder",
            data={
                "name": item["partner_name"],
                "match_id": str(item["match_id"]),
            },
            db=db,
        )
        if result.get("sent"):
            sent += 1
            await reminder_service.mark_reminder_sent(
                item["match_id"], redis
            )
    log.info(
        "reply_reminders_run",
        candidates=len(candidates),
        sent=sent,
    )
    return {"candidates": len(candidates), "sent": sent}


@celery_app.task(name="app.tasks.reminder_tasks.send_reply_reminders")
def send_reply_reminders() -> dict:
    async def _run():
        # Le redis_pool doit être initialisé côté worker (cf. startup Celery).
        async with async_session() as db:
            return await _send_reply_reminders_async(db, redis_pool.client)

    return asyncio.run(_run())


__all__ = [
    "_send_reply_reminders_async",
    "send_reply_reminders",
]
