from __future__ import annotations

"""
Tests subscription_service — gel doux premium (§business-model).

Principe produit NON-négociable :
    Premium expiré = gel doux (is_active_in_matching=False), jamais suppression.
    Re-subscribe → tout réactivé intégralement.

Limites free : 1 lives + 1 works + 1 hangs + 3 interested, 5 spots max.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

from app.models.city import City
from app.models.quartier import Quartier
from app.models.spot import Spot
from app.models.subscription import Subscription
from app.models.user import User
from app.models.user_quartier import UserQuartier
from app.models.user_spot import UserSpot
from app.services import subscription_service
from app.utils.phone import hash_phone

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _seed_city_and_quartiers(db, nb_quartiers: int = 4) -> tuple[City, list[Quartier]]:
    city = City(
        id=uuid4(),
        name="Lomé",
        country_code="TG",
        country_name="Togo",
        timezone="Africa/Lome",
        currency_code="XOF",
        premium_price_monthly=5000,
        premium_price_weekly=1500,
        phase="launch",
        is_active=True,
    )
    db.add(city)
    await db.flush()
    qs = []
    for i in range(nb_quartiers):
        q = Quartier(
            id=uuid4(),
            name=f"Q{i}",
            city_id=city.id,
            latitude=6.15 + i * 0.01,
            longitude=1.21 + i * 0.01,
        )
        db.add(q)
        qs.append(q)
    await db.flush()
    return city, qs


async def _seed_spots(db, city_id, count: int = 7) -> list[Spot]:
    spots = []
    for i in range(count):
        s = Spot(
            id=uuid4(),
            name=f"Spot{i}",
            category="cafe",
            city_id=city_id,
            location=from_shape(Point(1.21 + 0.001 * i, 6.15), srid=4326),
            latitude=6.15,
            longitude=1.21 + 0.001 * i,
            is_verified=True,
            is_active=True,
        )
        db.add(s)
        spots.append(s)
    await db.flush()
    return spots


async def _make_user(db, city_id, is_premium=True) -> User:
    u = User(
        id=uuid4(),
        phone_hash=hash_phone(f"+2289{uuid4().int % 10_000_000:07d}"),
        phone_country_code="228",
        is_phone_verified=True,
        is_premium=is_premium,
        city_id=city_id,
    )
    db.add(u)
    await db.flush()
    return u


async def test_downgrade_freezes_quartiers_beyond_free_limit(db_session):
    """User premium avec 2 "lives" quartiers → un seul reste actif en free."""
    city, quartiers = await _seed_city_and_quartiers(db_session, 4)
    user = await _make_user(db_session, city.id, is_premium=True)

    # 2 "lives" (premium-only : un seul autorisé en free)
    for q in quartiers[:2]:
        db_session.add(
            UserQuartier(
                user_id=user.id,
                quartier_id=q.id,
                relation_type="lives",
                is_active_in_matching=True,
            )
        )
    # 1 "works" : autorisé en free
    db_session.add(
        UserQuartier(
            user_id=user.id,
            quartier_id=quartiers[2].id,
            relation_type="works",
            is_active_in_matching=True,
        )
    )
    await db_session.commit()

    result = await subscription_service.downgrade_user_limits(user, db_session)
    assert result["quartiers_frozen"] == 1
    assert user.is_premium is False

    from sqlalchemy import select

    rows = (
        await db_session.execute(
            select(UserQuartier).where(UserQuartier.user_id == user.id)
        )
    ).scalars().all()
    active_by_rel = {}
    for uq in rows:
        active_by_rel.setdefault(uq.relation_type, 0)
        if uq.is_active_in_matching:
            active_by_rel[uq.relation_type] += 1

    assert active_by_rel["lives"] == 1
    assert active_by_rel["works"] == 1


async def test_downgrade_freezes_spots_beyond_5(db_session):
    city, _ = await _seed_city_and_quartiers(db_session, 1)
    spots = await _seed_spots(db_session, city.id, count=7)
    user = await _make_user(db_session, city.id, is_premium=True)

    for s in spots:
        db_session.add(
            UserSpot(
                user_id=user.id,
                spot_id=s.id,
                fidelity_level="regular",
                fidelity_score=0.5,
                is_active_in_matching=True,
            )
        )
    await db_session.commit()

    result = await subscription_service.downgrade_user_limits(user, db_session)
    assert result["spots_frozen"] == 2

    from sqlalchemy import select

    active = (
        await db_session.execute(
            select(UserSpot).where(
                UserSpot.user_id == user.id,
                UserSpot.is_active_in_matching.is_(True),
            )
        )
    ).scalars().all()
    assert len(active) == 5


async def test_upgrade_reactivates_all(db_session):
    city, quartiers = await _seed_city_and_quartiers(db_session, 2)
    spots = await _seed_spots(db_session, city.id, count=3)
    user = await _make_user(db_session, city.id, is_premium=False)

    # Simule un gel : tous les items avec is_active_in_matching=False
    for q in quartiers:
        db_session.add(
            UserQuartier(
                user_id=user.id,
                quartier_id=q.id,
                relation_type="hangs",
                is_active_in_matching=False,
            )
        )
    for s in spots:
        db_session.add(
            UserSpot(
                user_id=user.id,
                spot_id=s.id,
                fidelity_level="regular",
                fidelity_score=0.4,
                is_active_in_matching=False,
            )
        )
    await db_session.commit()

    result = await subscription_service.upgrade_user_limits(user, db_session)
    assert result["quartiers_reactivated"] == 2
    assert result["spots_reactivated"] == 3
    assert user.is_premium is True


async def test_downgrade_expired_subscriptions_batch(db_session):
    city, quartiers = await _seed_city_and_quartiers(db_session, 1)
    user = await _make_user(db_session, city.id, is_premium=True)
    db_session.add(
        UserQuartier(
            user_id=user.id,
            quartier_id=quartiers[0].id,
            relation_type="lives",
            is_active_in_matching=True,
        )
    )
    db_session.add(
        Subscription(
            id=uuid4(),
            user_id=user.id,
            plan="monthly",
            provider="paystack",
            payment_method="momo",
            is_active=True,
            starts_at=datetime.now(timezone.utc) - timedelta(days=60),
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            amount=5000,
            currency="XOF",
        )
    )
    await db_session.commit()

    result = await subscription_service.downgrade_expired_subscriptions(db_session)
    assert result["processed"] == 1

    await db_session.refresh(user)
    assert user.is_premium is False
