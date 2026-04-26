from __future__ import annotations

"""Celery tasks pour le mode voyage.

`expire_travel_modes` (cron 1h) :
  Pour chaque user dont travel_until <= now, désactive le voyage et pose
  travel_cooldown_until = now + 7j. Idempotent.
"""

import asyncio

import structlog

from app.celery_app import celery_app
from app.db.session import async_session
from app.services import travel_service

log = structlog.get_logger()


@celery_app.task(name="app.tasks.travel_tasks.expire_travel_modes")
def expire_travel_modes() -> int:
    """Wrapper sync qui démarre la coroutine async."""
    return asyncio.run(_expire_async())


async def _expire_async() -> int:
    async with async_session() as db:
        count = await travel_service.expire_due_travels(db)
    if count > 0:
        log.info("travel_modes_expired", count=count)
    return count
