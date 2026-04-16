from __future__ import annotations

"""Tests Cities / Countries / Waitlist join (MàJ villes/pays)."""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _seed_cities(db_session):
    from app.models.city import City

    lome = City(
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
        display_order=10,
        waitlist_threshold=500,
    )
    kara = City(
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
        display_order=20,
        waitlist_threshold=500,
    )
    hidden = City(
        id=uuid4(),
        name="Atakpamé",
        country_code="TG",
        country_name="Togo",
        country_flag="🇹🇬",
        phone_prefix="+228",
        timezone="Africa/Lome",
        currency_code="XOF",
        premium_price_monthly=5000,
        premium_price_weekly=1500,
        phase="hidden",
    )
    abidjan = City(
        id=uuid4(),
        name="Abidjan",
        country_code="CI",
        country_name="Côte d'Ivoire",
        country_flag="🇨🇮",
        phone_prefix="+225",
        timezone="Africa/Abidjan",
        currency_code="XOF",
        premium_price_monthly=5000,
        premium_price_weekly=1500,
        phase="launch",
        display_order=10,
    )
    db_session.add_all([lome, kara, hidden, abidjan])
    await db_session.commit()
    return {"lome": lome, "kara": kara, "hidden": hidden, "abidjan": abidjan}


async def test_list_cities_by_country_filters_hidden(client, db_session):
    cities = await _seed_cities(db_session)
    resp = await client.get("/cities?country_code=TG")
    assert resp.status_code == 200
    body = resp.json()
    names = [c["name"] for c in body["cities"]]
    assert "Lomé" in names and "Kara" in names
    assert "Atakpamé" not in names  # hidden masquée

    # Lomé = launch → selectable, sans waitlist
    lome = next(c for c in body["cities"] if c["name"] == "Lomé")
    assert lome["selectable"] is True
    assert lome["waitlist"] is None

    # Kara = teaser → non-selectable, avec waitlist
    kara = next(c for c in body["cities"] if c["name"] == "Kara")
    assert kara["selectable"] is False
    assert kara["waitlist"]["threshold"] == 500
    assert kara["waitlist"]["total_registered"] == 0


async def test_list_countries(client, db_session):
    await _seed_cities(db_session)
    resp = await client.get("/countries")
    assert resp.status_code == 200
    body = resp.json()
    codes = {c["country_code"] for c in body["countries"]}
    assert codes == {"TG", "CI"}  # hidden-only pays exclus
    tg = next(c for c in body["countries"] if c["country_code"] == "TG")
    assert tg["active_cities_count"] == 1  # Lomé
    assert tg["teaser_cities_count"] == 1  # Kara


async def test_launch_status(client, db_session):
    cities = await _seed_cities(db_session)
    resp = await client.get(f"/cities/{cities['lome'].id}/launch-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["phase"] == "launch"
    assert body["total_registered"] == 0
    assert body["remaining_to_launch"] == 500


async def test_join_waitlist_female_skips(
    client, auth_headers, db_session, test_user
):
    """Femme → status='activated', position=0."""
    cities = await _seed_cities(db_session)
    # Profile woman
    from datetime import date
    from app.models.profile import Profile

    profile = Profile(
        user_id=test_user.id,
        display_name="Ama",
        birth_date=date(2000, 1, 1),
        gender="woman",
        seeking_gender="men",
        intention="serious",
        sector="tech",
    )
    db_session.add(profile)
    test_user.profile = profile
    await db_session.commit()

    resp = await client.post(
        f"/cities/{cities['kara'].id}/waitlist/join",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "activated"
    assert body["position"] == 0


async def test_join_waitlist_male_gets_position(
    client, auth_headers, db_session, test_user
):
    """Homme → status='waiting' avec position."""
    cities = await _seed_cities(db_session)
    from datetime import date
    from app.models.profile import Profile

    profile = Profile(
        user_id=test_user.id,
        display_name="Kofi",
        birth_date=date(1995, 5, 10),
        gender="man",
        seeking_gender="women",
        intention="serious",
        sector="tech",
    )
    db_session.add(profile)
    test_user.profile = profile
    await db_session.commit()

    resp = await client.post(
        f"/cities/{cities['kara'].id}/waitlist/join",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "waiting"
    assert body["position"] == 1


async def test_join_hidden_city_404(client, auth_headers, db_session):
    cities = await _seed_cities(db_session)
    resp = await client.post(
        f"/cities/{cities['hidden'].id}/waitlist/join",
        headers=auth_headers,
    )
    assert resp.status_code == 404
