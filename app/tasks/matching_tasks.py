from __future__ import annotations

"""
Matching batch tasks (spec §7 + §38 timezones).

Celery n'est pas encore câblé (S10). Ces fonctions sont `async def`
directement appelables (par un test, par /admin/matching/trigger-batch,
ou en S10 décorées `@celery_app.task` et invoquées via `.delay()`).

Le bucketing par timezone est IMPLÉMENTÉ DANS LA FONCTION (pas juste
en TODO). En S10, Celery Beat planifiera une tâche horaire qui appelle
`generate_all_feeds(trigger_utc_hour=datetime.utcnow().hour)` — seules
les villes dont l'heure locale est dans la fenêtre nocturne (3h-5h)
seront traitées.

Fenêtre nocturne choisie : [3h, 5h[ locale, alignée sur l'activité
minimale dans toutes les villes ciblées (Lomé, Abidjan, Accra, Dakar,
Lagos, Nairobi). Un override `window` est passable en paramètre pour
les tests.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

import redis.asyncio as aioredis
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.core.constants import MATCHING_ACTIVE_WINDOW_DAYS
from app.db.redis import redis_pool
from app.db.session import async_session
from app.models.city import City
from app.models.user import User
from app.services.feed_service import _write_feed_cache
from app.services.matching_engine.pipeline import generate_feed_for_user

log = structlog.get_logger()


# Fenêtre locale durant laquelle on génère les feeds
_DEFAULT_LOCAL_WINDOW: tuple[int, int] = (3, 5)  # [3h, 5h[


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════


def _local_hour_for_city(city: City, now_utc: datetime) -> int:
    """
    Convertit now_utc dans la timezone IANA de la ville et retourne
    l'heure locale (0-23). DST-safe grâce à zoneinfo.
    """
    try:
        zi = ZoneInfo(city.timezone)
    except Exception:
        # Timezone inconnue → retombe sur UTC (pas d'exception dans le batch)
        zi = ZoneInfo("UTC")
    return now_utc.astimezone(zi).hour


async def _get_active_cities(db: AsyncSession) -> list[City]:
    rows = await db.execute(
        select(City).where(
            City.is_active.is_(True),
            City.phase.in_(("teaser", "launch", "growth", "stable")),
        )
    )
    return list(rows.scalars().all())


async def _get_active_users_for_city(
    city_id: UUID, db: AsyncSession
) -> list[UUID]:
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=MATCHING_ACTIVE_WINDOW_DAYS
    )
    rows = await db.execute(
        select(User.id).where(
            User.city_id == city_id,
            User.is_active.is_(True),
            User.is_visible.is_(True),
            User.is_banned.is_(False),
            User.is_deleted.is_(False),
            User.is_selfie_verified.is_(True),
            User.last_active_at >= cutoff,
        )
    )
    return [r[0] for r in rows.all()]


async def _generate_for_city(
    city: City, db: AsyncSession, redis_client: aioredis.Redis
) -> int:
    """Retourne le nombre de users traités pour la ville."""
    user_ids = await _get_active_users_for_city(city.id, db)
    processed = 0
    for uid in user_ids:
        feed = await generate_feed_for_user(uid, db, redis_client)
        if not feed["profile_ids"]:
            continue
        await _write_feed_cache(uid, feed, redis_client, db)
        processed += 1
    if processed:
        await db.commit()
    return processed


# ══════════════════════════════════════════════════════════════════════
# Tâches publiques (async def appelables ; Celery wrappera en S10)
# ══════════════════════════════════════════════════════════════════════


async def generate_single_feed(
    user_id: UUID,
    db: AsyncSession,
    redis_client: aioredis.Redis,
) -> dict:
    """
    Regénère le feed d'UN user. Persiste Redis (feed:{uid} 24h) + FeedCache DB.
    Retourne le dict brut du pipeline ({profile_ids, wildcards, new_users}).
    """
    feed = await generate_feed_for_user(user_id, db, redis_client)
    await _write_feed_cache(user_id, feed, redis_client, db)
    await db.commit()
    log.info(
        "feed_regenerated",
        user_id=str(user_id),
        count=len(feed["profile_ids"]),
    )
    return feed


async def generate_all_feeds(
    db: AsyncSession,
    redis_client: aioredis.Redis,
    *,
    trigger_utc_hour: int | None = None,
    local_window: tuple[int, int] = _DEFAULT_LOCAL_WINDOW,
) -> dict:
    """
    Batch nocturne : génère les feeds de toutes les villes éligibles.

    Bucketing timezone :
      - Si `trigger_utc_hour is None` (trigger manuel / test) → traite
        toutes les villes actives sans filtre.
      - Sinon → ne traite qu'une ville si son heure locale est dans
        `local_window` (inclusif → exclusif). Celery Beat appellera
        cette fonction toutes les heures avec `trigger_utc_hour=X`.

    Retour :
        {
            "cities_processed": [UUID, ...],
            "cities_skipped":   [UUID, ...],
            "users_processed":  int,
            "duration_s":       float,
        }
    """
    start = datetime.now(timezone.utc)
    cities = await _get_active_cities(db)

    processed: list[UUID] = []
    skipped: list[UUID] = []
    users_total = 0

    for city in cities:
        if trigger_utc_hour is not None:
            local_h = _local_hour_for_city(city, start)
            lo, hi = local_window
            if not (lo <= local_h < hi):
                skipped.append(city.id)
                continue
        try:
            n = await _generate_for_city(city, db, redis_client)
            users_total += n
            processed.append(city.id)
        except Exception as exc:  # noqa: BLE001 — log + continue
            log.error(
                "feed_batch_city_failed",
                city_id=str(city.id),
                error=str(exc),
            )
            skipped.append(city.id)

    duration = (datetime.now(timezone.utc) - start).total_seconds()
    log.info(
        "feed_batch_done",
        cities_processed=len(processed),
        cities_skipped=len(skipped),
        users_processed=users_total,
        duration_s=round(duration, 2),
    )
    return {
        "cities_processed": processed,
        "cities_skipped": skipped,
        "users_processed": users_total,
        "duration_s": duration,
    }


# ══════════════════════════════════════════════════════════════════════
# Celery wrappers (§S12)
# ══════════════════════════════════════════════════════════════════════


@celery_app.task(name="app.tasks.matching_tasks.generate_all_feeds")
def generate_all_feeds_task() -> dict:
    """Entry point Celery Beat (daily 3h UTC)."""

    async def _run():
        async with async_session() as db:
            result = await generate_all_feeds(
                db,
                redis_pool.client,
                trigger_utc_hour=datetime.now(timezone.utc).hour,
            )
        return {
            "cities_processed": [str(x) for x in result["cities_processed"]],
            "cities_skipped": [str(x) for x in result["cities_skipped"]],
            "users_processed": result["users_processed"],
            "duration_s": result["duration_s"],
        }

    return asyncio.run(_run())


__all__ = [
    "generate_single_feed",
    "generate_all_feeds",
    "generate_all_feeds_task",
    "_local_hour_for_city",
]
