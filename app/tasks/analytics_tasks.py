from __future__ import annotations

"""
Analytics tasks (§29, S13).

compute_daily_kpis : quotidien (00h30 UTC).

Calcule 8 metriques pour chaque ville active + une ligne globale
(city_id=None). Upsert idempotent via INSERT ... ON CONFLICT UPDATE.

Metriques :
  signups, signups_completed, daily_active, likes, matches,
  messages, premium_count, reports.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import structlog
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.db.session import async_session
from app.models.city import City
from app.models.daily_kpi import DailyKpi
from app.models.match import Match
from app.models.message import Message
from app.models.report import Report
from app.models.user import User

log = structlog.get_logger()


async def _upsert_kpi(
    db: AsyncSession,
    d: date,
    city_id: UUID | None,
    metric: str,
    value: float,
) -> None:
    """INSERT ... ON CONFLICT (date, city_id, metric) DO UPDATE SET value."""
    stmt = pg_insert(DailyKpi).values(
        id=uuid4(),
        date=d,
        city_id=city_id,
        metric=metric,
        value=value,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["date", "city_id", "metric"],
        set_={"value": stmt.excluded.value},
    )
    await db.execute(stmt)


async def _count(db: AsyncSession, stmt) -> int:
    result = await db.scalar(stmt)
    return result or 0


async def _compute_for_city(
    db: AsyncSession,
    d: date,
    city_id: UUID | None,
) -> dict[str, float]:
    """Calcule les 8 metriques pour une ville (ou global si None)."""
    day_start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    def _city_filter(model_class):
        """Filtre par ville si applicable."""
        if city_id is None:
            return []
        if hasattr(model_class, "city_id"):
            return [model_class.city_id == city_id]
        return []

    metrics: dict[str, float] = {}

    # signups
    q = select(func.count(User.id)).where(
        User.created_at >= day_start,
        User.created_at < day_end,
        *_city_filter(User),
    )
    metrics["signups"] = float(await _count(db, q))

    # signups_completed (onboarding terminé)
    q = select(func.count(User.id)).where(
        User.created_at >= day_start,
        User.created_at < day_end,
        User.onboarding_step == "completed",
        *_city_filter(User),
    )
    metrics["signups_completed"] = float(await _count(db, q))

    # daily_active
    q = select(func.count(User.id)).where(
        User.last_active_at >= day_start,
        User.last_active_at < day_end,
        User.is_deleted.is_(False),
        *_city_filter(User),
    )
    metrics["daily_active"] = float(await _count(db, q))

    # likes (tous les matches crees ce jour, pending ou matched)
    if city_id is not None:
        q = (
            select(func.count(Match.id))
            .join(User, User.id == Match.user_a_id)
            .where(
                Match.created_at >= day_start,
                Match.created_at < day_end,
                User.city_id == city_id,
            )
        )
    else:
        q = select(func.count(Match.id)).where(
            Match.created_at >= day_start,
            Match.created_at < day_end,
        )
    metrics["likes"] = float(await _count(db, q))

    # matches (status == matched)
    if city_id is not None:
        q = (
            select(func.count(Match.id))
            .join(User, User.id == Match.user_a_id)
            .where(
                Match.created_at >= day_start,
                Match.created_at < day_end,
                Match.status == "matched",
                User.city_id == city_id,
            )
        )
    else:
        q = select(func.count(Match.id)).where(
            Match.created_at >= day_start,
            Match.created_at < day_end,
            Match.status == "matched",
        )
    metrics["matches"] = float(await _count(db, q))

    # messages
    q = select(func.count(Message.id)).where(
        Message.created_at >= day_start,
        Message.created_at < day_end,
    )
    # Messages n'ont pas de city_id direct — on ne filtre par ville
    # que si on peut joindre via sender. Pour le global c'est simple.
    if city_id is not None:
        q = (
            select(func.count(Message.id))
            .join(User, User.id == Message.sender_id)
            .where(
                Message.created_at >= day_start,
                Message.created_at < day_end,
                User.city_id == city_id,
            )
        )
    metrics["messages"] = float(await _count(db, q))

    # premium_count (snapshot du jour)
    q = select(func.count(User.id)).where(
        User.is_premium.is_(True),
        User.is_deleted.is_(False),
        *_city_filter(User),
    )
    metrics["premium_count"] = float(await _count(db, q))

    # reports
    q = select(func.count(Report.id)).where(
        Report.created_at >= day_start,
        Report.created_at < day_end,
    )
    metrics["reports"] = float(await _count(db, q))

    return metrics


async def _compute_daily_kpis_async(
    target_date: date | None = None,
) -> dict:
    """
    Calcule les KPIs du jour et les stocke dans DailyKpi.
    Idempotent : upsert.
    """
    d = target_date or (datetime.now(timezone.utc) - timedelta(days=1)).date()

    async with async_session() as db:
        # Toutes les villes actives
        city_rows = await db.execute(
            select(City).where(City.is_active.is_(True))
        )
        cities = list(city_rows.scalars().all())

        total_upserted = 0

        # Global (city_id=None)
        global_metrics = await _compute_for_city(db, d, None)
        for metric_name, value in global_metrics.items():
            await _upsert_kpi(db, d, None, metric_name, value)
            total_upserted += 1

        # Par ville
        for city in cities:
            city_metrics = await _compute_for_city(db, d, city.id)
            for metric_name, value in city_metrics.items():
                await _upsert_kpi(db, d, city.id, metric_name, value)
                total_upserted += 1

        await db.commit()

    log.info(
        "daily_kpis_computed",
        date=str(d),
        cities=len(cities),
        upserted=total_upserted,
    )
    return {"date": str(d), "cities": len(cities), "upserted": total_upserted}


@celery_app.task(name="app.tasks.analytics_tasks.compute_daily_kpis")
def compute_daily_kpis() -> dict:
    return asyncio.run(_compute_daily_kpis_async())


__all__ = ["_compute_daily_kpis_async", "compute_daily_kpis"]
