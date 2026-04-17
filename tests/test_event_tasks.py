from __future__ import annotations

"""Tests Event tasks (S14.7)."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import select

from app.models.city import City
from app.models.event import Event
from app.models.event_registration import EventRegistration
from app.models.spot import Spot
from app.models.user import User
from app.utils.phone import hash_phone

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _seed_event(db, *, status="published", starts_in_hours=1.5):
    city = City(
        id=uuid4(), name="Lomé", country_code="TG", country_name="Togo",
        timezone="Africa/Lome", currency_code="XOF",
        premium_price_monthly=5000, premium_price_weekly=1500,
        phase="launch", is_active=True,
    )
    db.add(city)
    await db.flush()

    spot = Spot(
        id=uuid4(), name="Test Spot", category="cafe", city_id=city.id,
        location=from_shape(Point(1.2, 6.1), srid=4326),
        latitude=6.1, longitude=1.2, is_verified=True, is_active=True,
    )
    db.add(spot)
    await db.flush()

    user = User(
        id=uuid4(), phone_hash=hash_phone("+22899990001"),
        phone_country_code="228", is_phone_verified=True,
        is_active=True, city_id=city.id,
    )
    db.add(user)
    await db.flush()

    now = datetime.now(timezone.utc)
    event = Event(
        id=uuid4(), title="Test Event", category="social",
        spot_id=spot.id, city_id=city.id,
        starts_at=now + timedelta(hours=starts_in_hours),
        ends_at=now + timedelta(hours=starts_in_hours + 3),
        status=status, is_active=True, is_approved=True,
    )
    db.add(event)
    await db.flush()

    reg = EventRegistration(
        event_id=event.id, user_id=user.id, status="registered",
    )
    db.add(reg)
    await db.commit()
    return {"event": event, "user": user, "reg": reg}


async def test_event_status_updater_transitions(db_session, redis_client):
    """published → ongoing when starts_at < now, ongoing → completed when ends_at < now."""
    from app.tasks.event_tasks import _event_status_updater_async
    from contextlib import asynccontextmanager
    from unittest.mock import patch

    # Event that started 1h ago, ends in 2h → should become ongoing
    data = await _seed_event(db_session, status="published", starts_in_hours=-1)
    event = data["event"]

    @asynccontextmanager
    async def _fake_session():
        yield db_session

    with patch("app.tasks.event_tasks.async_session", _fake_session):
        result = await _event_status_updater_async()

    assert result["to_ongoing"] >= 1

    await db_session.refresh(event)
    assert event.status == "ongoing"


async def test_event_reminder_marks_sent(db_session, redis_client):
    """Reminder envoye pour un event dans < 2h, reminder_sent_at set."""
    from app.tasks.event_tasks import _event_reminder_async
    from contextlib import asynccontextmanager
    from unittest.mock import patch

    data = await _seed_event(db_session, status="published", starts_in_hours=1.5)
    event = data["event"]

    assert event.reminder_sent_at is None

    @asynccontextmanager
    async def _fake_session():
        yield db_session

    with patch("app.tasks.event_tasks.async_session", _fake_session):
        result = await _event_reminder_async()

    assert result["events"] >= 1
    assert result["reminded"] >= 1

    await db_session.refresh(event)
    assert event.reminder_sent_at is not None
