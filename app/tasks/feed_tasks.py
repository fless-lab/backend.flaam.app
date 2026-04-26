from __future__ import annotations

"""
Feed-related Celery tasks.

`send_daily_feed_pushes` (cron toutes les 15 min) :
  Pour chaque user dont `notification_pref.daily_feed_hour` correspond à
  l'heure courante dans la timezone de sa ville, envoie un push
  `notif_daily_feed`. Idempotent par jour (dedup Redis 24h) pour éviter
  les doublons si la task tourne plusieurs fois dans la même heure.

  Le push est gated automatiquement par `send_push` :
    - daily_feed pref off → pas de push
    - quiet_hours active   → pas de push (l'user verra sa notif après)
    - is_banned/deleted/inactive → pas de push
"""

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import redis.asyncio as aioredis
import structlog
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.celery_app import celery_app
from app.core.config import get_settings
from app.db.session import async_session
from app.models.notification_preference import NotificationPreference
from app.models.user import User
from app.services import notification_service

log = structlog.get_logger()

# Une seule push par user par jour (calendrier UTC). Évite le doublon si
# la beat task tourne 2× dans la même fenêtre horaire (ex: drift Celery).
_DEDUP_KEY = "daily_feed_pushed:{user_id}:{day}"
_DEDUP_TTL_SECONDS = 28 * 3600  # un peu plus que 24h, marge de sécurité.


def _local_hour(user: User, now_utc: datetime) -> int:
    tz_name = user.city.timezone if user.city is not None else "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = timezone.utc
    return now_utc.astimezone(tz).hour


async def _send_daily_feed_pushes_async() -> dict:
    settings = get_settings()
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    now_utc = datetime.now(timezone.utc)
    today_str = now_utc.date().isoformat()

    sent = 0
    skipped_dedup = 0
    skipped_other = 0

    try:
        async with async_session() as db:
            result = await db.execute(
                select(User)
                .options(
                    selectinload(User.city),
                    selectinload(User.notification_prefs),
                )
                .where(
                    User.is_active.is_(True),
                    User.is_visible.is_(True),
                    User.is_deleted.is_(False),
                    User.is_banned.is_(False),
                    User.onboarding_step == "completed",
                ),
            )
            users = list(result.scalars())

            for user in users:
                prefs: NotificationPreference | None = user.notification_prefs
                if prefs is None or not prefs.daily_feed:
                    skipped_other += 1
                    continue

                target_hour = prefs.daily_feed_hour
                if _local_hour(user, now_utc) != target_hour:
                    skipped_other += 1
                    continue

                # Dedup : 1 push par user par jour
                key = _DEDUP_KEY.format(user_id=str(user.id), day=today_str)
                if await redis_client.exists(key):
                    skipped_dedup += 1
                    continue

                res = await notification_service.send_push(
                    user.id, type="notif_daily_feed", db=db,
                )
                if res.get("sent"):
                    await redis_client.set(key, "1", ex=_DEDUP_TTL_SECONDS)
                    sent += 1
                else:
                    skipped_other += 1
    finally:
        await redis_client.aclose()

    log.info(
        "daily_feed_pushes_done",
        sent=sent,
        skipped_dedup=skipped_dedup,
        skipped_other=skipped_other,
    )
    return {
        "sent": sent,
        "skipped_dedup": skipped_dedup,
        "skipped_other": skipped_other,
    }


@celery_app.task(name="app.tasks.feed_tasks.send_daily_feed_pushes")
def send_daily_feed_pushes_task() -> dict:
    return asyncio.run(_send_daily_feed_pushes_async())


__all__ = ["send_daily_feed_pushes_task"]
