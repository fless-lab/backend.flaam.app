from __future__ import annotations

"""Tests Invite codes + Waitlist skip (MàJ 7)."""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_city(db_session, phase: str = "launch"):
    from app.models.city import City

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
        phase=phase,
    )
    db_session.add(city)
    await db_session.commit()
    return city


async def _female_profile(db_session, user, name: str = "Ama"):
    from app.models.profile import Profile

    profile = Profile(
        user_id=user.id,
        display_name=name,
        birth_date=date(2000, 1, 1),
        gender="woman",
        seeking_gender="men",
        intention="serious",
        sector="tech",
    )
    db_session.add(profile)
    user.profile = profile
    await db_session.commit()
    return profile


async def test_generate_codes_female_gets_three(
    client, auth_headers, db_session, test_user
):
    city = await _make_city(db_session)
    test_user.city_id = city.id
    await _female_profile(db_session, test_user)

    resp = await client.post("/invites/generate", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    for ic in body["codes"]:
        assert ic["code"].startswith("FLAAM-")
        assert len(ic["code"]) == 14  # FLAAM- + 8 chars


async def test_generate_codes_male_refused(
    client, auth_headers, db_session, test_user
):
    city = await _make_city(db_session)
    test_user.city_id = city.id
    from app.models.profile import Profile

    profile = Profile(
        user_id=test_user.id,
        display_name="Kofi",
        birth_date=date(1995, 1, 1),
        gender="man",
        seeking_gender="women",
        intention="serious",
        sector="tech",
    )
    db_session.add(profile)
    test_user.profile = profile
    await db_session.commit()

    resp = await client.post("/invites/generate", headers=auth_headers)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "invite_codes_not_available"


async def test_generate_codes_ambassador_gets_fifty(
    client, auth_headers, db_session, test_user
):
    city = await _make_city(db_session)
    test_user.city_id = city.id
    test_user.is_ambassador = True
    from app.models.profile import Profile

    profile = Profile(
        user_id=test_user.id,
        display_name="Afi",
        birth_date=date(1990, 1, 1),
        gender="woman",
        seeking_gender="men",
        intention="serious",
        sector="commerce",
    )
    db_session.add(profile)
    test_user.profile = profile
    await db_session.commit()

    resp = await client.post("/invites/generate", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 50


async def test_validate_code_ok(client, auth_headers, db_session, test_user):
    from app.models.invite_code import InviteCode

    city = await _make_city(db_session)
    test_user.city_id = city.id
    await _female_profile(db_session, test_user)

    ic = InviteCode(
        code="FLAAM-ABCDEF12",
        creator_id=test_user.id,
        city_id=city.id,
        type="standard",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        is_active=True,
    )
    db_session.add(ic)
    await db_session.commit()

    resp = await client.post(
        "/invites/validate",
        json={"code": "FLAAM-ABCDEF12"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["city_name"] == "Lomé"
    assert body["creator_name"] == "Ama"


async def test_validate_code_not_found(
    client, auth_headers, db_session
):
    resp = await client.post(
        "/invites/validate",
        json={"code": "FLAAM-NOPE1234"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is False
    assert resp.json()["reason"] == "not_found"


async def test_validate_code_expired(client, auth_headers, db_session, test_user):
    from app.models.invite_code import InviteCode

    city = await _make_city(db_session)
    test_user.city_id = city.id
    await _female_profile(db_session, test_user)

    ic = InviteCode(
        code="FLAAM-EXPIRED1",
        creator_id=test_user.id,
        city_id=city.id,
        type="standard",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        is_active=True,
    )
    db_session.add(ic)
    await db_session.commit()

    resp = await client.post(
        "/invites/validate",
        json={"code": "FLAAM-EXPIRED1"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is False
    assert resp.json()["reason"] == "expired"


async def test_redeem_code_skips_waitlist(
    client, auth_headers, db_session, test_user
):
    """Un homme avec un code valide doit être activé même en teaser."""
    from app.models.invite_code import InviteCode
    from app.models.profile import Profile
    from app.models.user import User
    from app.utils.phone import hash_phone

    city = await _make_city(db_session, phase="teaser")

    # Creator : une femme différente de test_user
    creator = User(
        phone_hash=hash_phone("+22800000001"),
        phone_country_code="228",
        city_id=city.id,
    )
    db_session.add(creator)
    await db_session.flush()
    creator_profile = Profile(
        user_id=creator.id,
        display_name="Ama",
        birth_date=date(2000, 1, 1),
        gender="woman",
        seeking_gender="men",
        intention="serious",
        sector="tech",
    )
    db_session.add(creator_profile)

    ic = InviteCode(
        code="FLAAM-REDEEM12",
        creator_id=creator.id,
        city_id=city.id,
        type="standard",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        is_active=True,
    )
    db_session.add(ic)

    # test_user = homme
    test_user.city_id = city.id
    profile = Profile(
        user_id=test_user.id,
        display_name="Kofi",
        birth_date=date(1995, 1, 1),
        gender="man",
        seeking_gender="women",
        intention="serious",
        sector="tech",
    )
    db_session.add(profile)
    test_user.profile = profile
    await db_session.commit()

    resp = await client.post(
        "/invites/redeem",
        json={"code": "FLAAM-REDEEM12"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["redeemed"] is True
    assert body["waitlist_status"] == "activated"


async def test_redeem_code_already_used(
    client, auth_headers, db_session, test_user
):
    from app.models.invite_code import InviteCode
    from app.models.user import User
    from app.utils.phone import hash_phone

    city = await _make_city(db_session)
    other = User(
        phone_hash=hash_phone("+22800000002"),
        phone_country_code="228",
        city_id=city.id,
    )
    db_session.add(other)
    await db_session.flush()

    ic = InviteCode(
        code="FLAAM-USED1234",
        creator_id=other.id,
        city_id=city.id,
        type="standard",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        is_active=False,
        used_by_id=other.id,
        used_at=datetime.now(timezone.utc),
    )
    db_session.add(ic)
    test_user.city_id = city.id
    await db_session.commit()

    resp = await client.post(
        "/invites/redeem",
        json={"code": "FLAAM-USED1234"},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"].startswith("code_")
