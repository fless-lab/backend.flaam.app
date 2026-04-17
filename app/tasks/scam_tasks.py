from __future__ import annotations

"""
Scam risk batch (§39, §S12).

compute_scam_risk_batch : toutes les 24h.
- Parcourt les users actifs non-bannis.
- Recalcule compute_scam_risk pour chacun.
- Auto-ban si score > AUTO_BAN_THRESHOLD (0.70 actuel ; le seuil batch
  peut être plus strict — ici on utilise 0.90 pour minimiser les faux
  positifs batch, la détection report-par-report garde 0.70).
- Log si score > REVIEW_THRESHOLD (0.40).
"""

import asyncio

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.db.session import async_session
from app.models.user import User
from app.services import scam_detection_service

log = structlog.get_logger()


BATCH_AUTO_BAN_THRESHOLD = 0.90
BATCH_FLAG_THRESHOLD = 0.70


async def _compute_scam_risk_batch_async(db: AsyncSession) -> dict:
    """
    Retourne : {"scanned": int, "flagged": int, "auto_banned": int}.
    """
    rows = (
        await db.execute(
            select(User).where(
                User.is_active.is_(True),
                User.is_banned.is_(False),
                User.is_deleted.is_(False),
            )
        )
    ).scalars().all()

    scanned = 0
    flagged = 0
    auto_banned = 0
    for user in rows:
        scanned += 1
        score = await scam_detection_service.compute_scam_risk(user.id, db)
        if score > BATCH_AUTO_BAN_THRESHOLD:
            user.is_banned = True
            user.ban_reason = f"auto_scam_batch:{score:.2f}"
            auto_banned += 1
            log.warning(
                "auto_ban_scam_batch",
                user_id=str(user.id),
                score=score,
            )
        elif score > BATCH_FLAG_THRESHOLD:
            flagged += 1
            log.warning(
                "scam_flagged_batch",
                user_id=str(user.id),
                score=score,
            )

    await db.commit()
    return {
        "scanned": scanned,
        "flagged": flagged,
        "auto_banned": auto_banned,
    }


@celery_app.task(name="app.tasks.scam_tasks.compute_scam_risk_batch")
def compute_scam_risk_batch() -> dict:
    async def _run():
        async with async_session() as db:
            return await _compute_scam_risk_batch_async(db)

    return asyncio.run(_run())


__all__ = [
    "_compute_scam_risk_batch_async",
    "compute_scam_risk_batch",
    "BATCH_AUTO_BAN_THRESHOLD",
    "BATCH_FLAG_THRESHOLD",
]
