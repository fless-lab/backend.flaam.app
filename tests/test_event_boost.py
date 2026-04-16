from __future__ import annotations

"""Tests de l'event boost (MàJ 8 Porte 3 §5)."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

from app.utils.phone import hash_phone

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _seed(db_session):
    from app.models.city import City
    from app.models.event import Event
    from app.models.event_registration import EventRegistration
    from app.models.spot import Spot
    from app.models.user import User

    city = City(
        id=uuid4(),
        name="Lomé",
        country_code="TG",
        country_name="Togo",
        country_flag="🇹🇬",
        phone_prefix="+228",
        timezone="Africa/Lome",
        currency_code="XOF",
        premium_price_monthly=5000,
        premium_price_weekly=1500,
        phase="launch",
        is_active=True,
    )
    db_session.add(city)
    await db_session.flush()

    spot = Spot(
        id=uuid4(),
        name="Venue",
        category="bar",
        city_id=city.id,
        location=from_shape(Point(1.2137, 6.1725), srid=4326),
        latitude=6.1725,
        longitude=1.2137,
        is_active=True,
    )
    db_session.add(spot)
    await db_session.flush()

    def _user(phone: str) -> User:
        u = User(
            phone_hash=hash_phone(phone),
            phone_country_code="228",
            city_id=city.id,
        )
        db_session.add(u)
        return u

    alice = _user("+22800100001")
    bob = _user("+22800100002")
    await db_session.flush()

    ev = Event(
        id=uuid4(),
        title="Afterwork Test",
        spot_id=spot.id,
        city_id=city.id,
        starts_at=datetime.now(timezone.utc) - timedelta(days=2),
        ends_at=datetime.now(timezone.utc) - timedelta(days=2, hours=-3),
        category="afterwork",
        is_approved=True,
        is_active=True,
        status="completed",
    )
    db_session.add(ev)
    await db_session.flush()

    db_session.add_all(
        [
            EventRegistration(
                event_id=ev.id, user_id=alice.id, status="checked_in"
            ),
            EventRegistration(
                event_id=ev.id, user_id=bob.id, status="checked_in"
            ),
        ]
    )
    await db_session.commit()
    return alice, bob, ev


async def test_event_boost_applies_to_co_attendees(db_session):
    from app.services.matching_engine.event_boost import compute_event_boosts

    alice, bob, _ = await _seed(db_session)

    boosts = await compute_event_boosts(alice.id, [bob.id], db_session)
    assert bob.id in boosts
    assert boosts[bob.id] == 15.0  # plateau complet (< 7 jours)


async def test_event_boost_decays_after_plateau(db_session):
    from app.services.matching_engine.event_boost import compute_event_boosts

    alice, bob, ev = await _seed(db_session)
    # Décale l'event à 10 jours → dans la fenêtre de decay
    ev.starts_at = datetime.now(timezone.utc) - timedelta(days=10)
    ev.ends_at = datetime.now(timezone.utc) - timedelta(days=10)
    await db_session.commit()

    boosts = await compute_event_boosts(alice.id, [bob.id], db_session)
    # À j+10 : remaining = 4/7 * 15 ≈ 8.57
    assert 0 < boosts[bob.id] < 15


async def test_event_boost_zero_after_14_days(db_session):
    from app.services.matching_engine.event_boost import compute_event_boosts

    alice, bob, ev = await _seed(db_session)
    ev.starts_at = datetime.now(timezone.utc) - timedelta(days=20)
    ev.ends_at = datetime.now(timezone.utc) - timedelta(days=20)
    await db_session.commit()

    boosts = await compute_event_boosts(alice.id, [bob.id], db_session)
    # Hors fenêtre : le filtre Event.starts_at >= cutoff élimine l'event
    assert boosts == {}


async def test_event_boost_ignored_when_not_checked_in(db_session):
    from app.models.event_registration import EventRegistration
    from app.services.matching_engine.event_boost import compute_event_boosts
    from sqlalchemy import update

    alice, bob, ev = await _seed(db_session)
    # Bob reste "registered" (pas checked_in)
    await db_session.execute(
        update(EventRegistration)
        .where(EventRegistration.user_id == bob.id)
        .values(status="registered")
    )
    await db_session.commit()

    boosts = await compute_event_boosts(alice.id, [bob.id], db_session)
    assert boosts == {}
