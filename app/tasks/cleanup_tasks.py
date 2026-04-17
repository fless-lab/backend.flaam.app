from __future__ import annotations

"""
Cleanup tasks (§17 RGPD, §S12 câblage Celery).

Tâches planifiées dans le beat schedule :
- purge_expired_matches          : toutes les 6h
- purge_old_behavior_logs        : hebdo (lundi 2h UTC)
- purge_old_feed_caches          : toutes les 12h (Redis + DB)
- cleanup_account_histories      : mensuel (1er du mois)

Politique de rétention :
- Match matched inactif > 7j (last_message_at) → status "expired"
- BehaviorLog > 90 jours → supprimé
- FeedCache : on ne purge rien en DB (index par date, peu volumineux)
  → on SCAN Redis et on laisse le TTL 24h faire le boulot.
- AccountHistory > 2 ans ET total_bans=0 → supprimé. Les bannis sont
  conservés indéfiniment (anti-récidive).
"""

import asyncio
from datetime import timedelta
from uuid import UUID

import structlog
from sqlalchemy import delete, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.core.cache import cache_invalidate_pattern
from app.db.redis import redis_pool
from app.db.session import async_session
from app.models.account_history import AccountHistory
from app.models.behavior_log import BehaviorLog
from app.models.match import Match

log = structlog.get_logger()


# ══════════════════════════════════════════════════════════════════════
# RGPD (stub conservé — impl complète en S13)
# ══════════════════════════════════════════════════════════════════════


async def purge_account_data(user_id: UUID, reason: str) -> None:
    """
    Pipeline RGPD Phase 2/3 — à implémenter en S13 (suppression physique
    des fichiers + DROP User row après T+30j).
    """
    log.info(
        "gdpr_purge_scheduled",
        user_id=str(user_id),
        reason=reason,
        note="phase 2/3 câblés en S13",
    )


# ══════════════════════════════════════════════════════════════════════
# purge_expired_matches
# ══════════════════════════════════════════════════════════════════════


async def _purge_expired_matches_async(db: AsyncSession) -> dict:
    """
    Match matched avec last_message_at < now - 7j → status "expired".
    """
    result = await db.execute(
        update(Match)
        .where(
            Match.last_message_at < func.now() - timedelta(days=7),
            Match.status == "matched",
        )
        .values(status="expired")
    )
    count = result.rowcount or 0
    log.info("purged_expired_matches", count=count)
    await db.commit()
    return {"count": count}


@celery_app.task(name="app.tasks.cleanup_tasks.purge_expired_matches")
def purge_expired_matches() -> dict:
    async def _run():
        async with async_session() as db:
            return await _purge_expired_matches_async(db)

    return asyncio.run(_run())


# ══════════════════════════════════════════════════════════════════════
# purge_old_behavior_logs
# ══════════════════════════════════════════════════════════════════════


async def _purge_old_behavior_logs_async(db: AsyncSession) -> dict:
    """BehaviorLog > 90j → DELETE."""
    result = await db.execute(
        delete(BehaviorLog).where(
            BehaviorLog.created_at < func.now() - timedelta(days=90)
        )
    )
    count = result.rowcount or 0
    log.info("purged_old_behavior_logs", count=count)
    await db.commit()
    return {"count": count}


@celery_app.task(name="app.tasks.cleanup_tasks.purge_old_behavior_logs")
def purge_old_behavior_logs() -> dict:
    async def _run():
        async with async_session() as db:
            return await _purge_old_behavior_logs_async(db)

    return asyncio.run(_run())


# ══════════════════════════════════════════════════════════════════════
# purge_old_feed_caches
# ══════════════════════════════════════════════════════════════════════


async def _purge_old_feed_caches_async(redis) -> dict:
    """
    SCAN feed:* et DEL. En pratique la plupart sont déjà expirés (TTL
    24h) — ce task nettoie les orphelins et force un refresh.
    """
    deleted = await cache_invalidate_pattern("feed:*", redis)
    log.info("purged_old_feed_caches", deleted=deleted)
    return {"deleted": deleted}


@celery_app.task(name="app.tasks.cleanup_tasks.purge_old_feed_caches")
def purge_old_feed_caches() -> dict:
    async def _run():
        return await _purge_old_feed_caches_async(redis_pool.client)

    return asyncio.run(_run())


# ══════════════════════════════════════════════════════════════════════
# cleanup_account_histories
# ══════════════════════════════════════════════════════════════════════


async def _cleanup_account_histories_async(db: AsyncSession) -> dict:
    """
    Supprime les AccountHistory inactives > 2 ans ET sans ban.

    Les bannis restent (anti-récidive via phone_hash / device_fingerprints).
    "Inactif" = pas de nouvel account créé avec ce phone_hash depuis 2 ans.
    """
    result = await db.execute(
        delete(AccountHistory)
        .where(
            AccountHistory.last_account_created_at
            < func.now() - timedelta(days=730),
            AccountHistory.total_bans == 0,
        )
    )
    count = result.rowcount or 0
    log.info("cleanup_account_histories", count=count)
    await db.commit()
    return {"count": count}


@celery_app.task(name="app.tasks.cleanup_tasks.cleanup_account_histories")
def cleanup_account_histories() -> dict:
    async def _run():
        async with async_session() as db:
            return await _cleanup_account_histories_async(db)

    return asyncio.run(_run())


__all__ = [
    "purge_account_data",
    "_purge_expired_matches_async",
    "purge_expired_matches",
    "_purge_old_behavior_logs_async",
    "purge_old_behavior_logs",
    "_purge_old_feed_caches_async",
    "purge_old_feed_caches",
    "_cleanup_account_histories_async",
    "cleanup_account_histories",
]
