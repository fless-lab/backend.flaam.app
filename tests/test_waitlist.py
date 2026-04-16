from __future__ import annotations

"""Tests waitlist — femmes skip directement, hommes entrent en file."""

from datetime import date
from uuid import uuid4

import pytest

from app.services import waitlist_service

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _teaser_city(db_session):
    from app.models.city import City

    city = City(
        id=uuid4(),
        name="Kara",
        country_code="TG",
        country_name="Togo",
        country_flag="🇹🇬",
        phone_prefix="+228",
        timezone="Africa/Lome",
        currency_code="XOF",
        premium_price_monthly=5000,
        premium_price_weekly=1500,
        phase="teaser",
    )
    db_session.add(city)
    await db_session.commit()
    return city


async def _attach_profile(db_session, user, *, gender: str, name: str):
    from app.models.profile import Profile

    profile = Profile(
        user_id=user.id,
        display_name=name,
        birth_date=date(1995, 1, 1),
        gender=gender,
        seeking_gender="men" if gender == "woman" else "women",
        intention="serious",
        sector="tech",
    )
    db_session.add(profile)
    user.profile = profile
    await db_session.commit()


async def test_woman_skips_waitlist_directly(db_session, test_user):
    city = await _teaser_city(db_session)
    test_user.city_id = city.id
    await _attach_profile(db_session, test_user, gender="woman", name="Ama")

    result = await waitlist_service.process_waitlist_join(
        test_user, city.id, db_session
    )
    assert result["status"] == "activated"
    assert result["position"] == 0
    assert "Bienvenue" in result["message"]


async def test_man_enters_waitlist(db_session, test_user):
    city = await _teaser_city(db_session)
    test_user.city_id = city.id
    await _attach_profile(db_session, test_user, gender="man", name="Kofi")

    result = await waitlist_service.process_waitlist_join(
        test_user, city.id, db_session
    )
    assert result["status"] == "waiting"
    assert result["position"] == 1
    assert result["total_waiting"] == 1
    assert "liste d'attente" in result["message"]


async def test_second_man_enters_waitlist_at_position_2(db_session, test_user):
    from app.models.user import User
    from app.utils.phone import hash_phone

    city = await _teaser_city(db_session)

    # 1er homme (pas test_user, un utilisateur à part)
    first = User(
        phone_hash=hash_phone("+22800000010"),
        phone_country_code="228",
        city_id=city.id,
    )
    db_session.add(first)
    await db_session.flush()
    await _attach_profile(db_session, first, gender="man", name="Kwame")

    r1 = await waitlist_service.process_waitlist_join(first, city.id, db_session)
    assert r1["status"] == "waiting"
    assert r1["position"] == 1
    assert r1["total_waiting"] == 1

    # 2e homme = test_user
    test_user.city_id = city.id
    await _attach_profile(db_session, test_user, gender="man", name="Kofi")

    r2 = await waitlist_service.process_waitlist_join(
        test_user, city.id, db_session
    )
    assert r2["status"] == "waiting"
    assert r2["position"] == 2
    assert r2["total_waiting"] == 2


async def test_man_with_invite_code_skips_waitlist(db_session, test_user):
    city = await _teaser_city(db_session)
    test_user.city_id = city.id
    await _attach_profile(db_session, test_user, gender="man", name="Kofi")

    result = await waitlist_service.process_waitlist_join(
        test_user, city.id, db_session, invite_code_used="FLAAM-TESTCODE"
    )
    assert result["status"] == "activated"
    assert result["position"] == 0
    assert "Code valide" in result["message"]
