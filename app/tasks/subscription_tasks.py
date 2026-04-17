from __future__ import annotations

"""
Subscription tasks (§S12, business-model "gel doux").

check_expired_subscriptions : toutes les heures.
- Détecte Subscription.expires_at < now AND is_active=True.
- Pour chaque : downgrade_user_limits (gel doux quartiers/spots) +
  push notif_premium_expired au user.
- Idempotent : une fois is_active=False, la sub n'est plus traitée.
"""

import asyncio
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.db.session import async_session
from app.models.subscription import Subscription
from app.models.user import User
from app.services import notification_service, subscription_service

log = structlog.get_logger()


async def _check_expired_subscriptions_async(db: AsyncSession) -> dict:
    """
    Boucle sur les subs expirées actives. Downgrade + push.

    Retourne : {"processed": int, "notified": int}.
    """
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(Subscription).where(
                Subscription.expires_at < now,
                Subscription.is_active.is_(True),
            )
        )
    ).scalars().all()

    processed = 0
    notified = 0
    for sub in rows:
        user = await db.get(User, sub.user_id)
        if user is None:
            continue
        sub.is_active = False
        # downgrade_user_limits commit interne → OK en prod, savepoint en test
        await subscription_service.downgrade_user_limits(user, db)
        processed += 1
        if user.is_active and not user.is_deleted:
            result = await notification_service.send_push(
                user.id,
                type="notif_premium_expired",
                data={},
                db=db,
            )
            if result.get("sent"):
                notified += 1
        log.info(
            "subscription_expired_downgraded",
            user_id=str(user.id),
            subscription_id=str(sub.id),
        )

    await db.commit()
    return {"processed": processed, "notified": notified}


@celery_app.task(name="app.tasks.subscription_tasks.check_expired_subscriptions")
def check_expired_subscriptions() -> dict:
    """Entry point Celery. Crée une session DB fraîche à chaque appel."""

    async def _run():
        async with async_session() as db:
            return await _check_expired_subscriptions_async(db)

    return asyncio.run(_run())


__all__ = [
    "_check_expired_subscriptions_async",
    "check_expired_subscriptions",
]
