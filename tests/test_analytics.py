from __future__ import annotations

"""Tests Analytics KPIs (§29, S13)."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.daily_kpi import DailyKpi
from app.tasks.analytics_tasks import _compute_daily_kpis_async
from tests._feed_setup import seed_ama_and_kofi

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_compute_daily_kpis_creates_rows(db_session, redis_client):
    """Creer quelques users → compute → verifier DailyKpi rows."""
    await seed_ama_and_kofi(db_session)
    today = datetime.now(timezone.utc).date()

    # Run directly with db_session (bypass async_session factory)
    from app.tasks.analytics_tasks import (
        _compute_for_city,
        _upsert_kpi,
    )

    # Compute global metrics
    metrics = await _compute_for_city(db_session, today, None)
    for metric_name, value in metrics.items():
        await _upsert_kpi(db_session, today, None, metric_name, value)
    await db_session.commit()

    # Verify rows created
    result = await db_session.execute(
        select(DailyKpi).where(DailyKpi.date == today, DailyKpi.city_id.is_(None))
    )
    rows = list(result.scalars().all())
    metric_names = {r.metric for r in rows}

    # Should have our 8 metrics
    expected = {
        "signups", "signups_completed", "daily_active", "likes",
        "matches", "messages", "premium_count", "reports",
    }
    assert expected.issubset(metric_names)


async def test_daily_kpi_upsert_idempotent(db_session, redis_client):
    """Lancer 2 fois → meme nombre de rows (pas de doublons)."""
    await seed_ama_and_kofi(db_session)
    today = datetime.now(timezone.utc).date()

    from app.tasks.analytics_tasks import _compute_for_city, _upsert_kpi

    # First pass
    metrics = await _compute_for_city(db_session, today, None)
    for name, value in metrics.items():
        await _upsert_kpi(db_session, today, None, name, value)
    await db_session.commit()

    result1 = await db_session.execute(
        select(DailyKpi).where(DailyKpi.date == today, DailyKpi.city_id.is_(None))
    )
    count1 = len(list(result1.scalars().all()))

    # Second pass (same date, same city_id=None)
    for name, value in metrics.items():
        await _upsert_kpi(db_session, today, None, name, value)
    await db_session.commit()

    result2 = await db_session.execute(
        select(DailyKpi).where(DailyKpi.date == today, DailyKpi.city_id.is_(None))
    )
    count2 = len(list(result2.scalars().all()))

    assert count1 == count2  # no duplicates
