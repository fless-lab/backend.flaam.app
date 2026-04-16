from __future__ import annotations

"""Tests Quartiers (§5.4)."""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _seed_city_with_quartiers(db_session, user):
    from app.models.city import City
    from app.models.quartier import Quartier
    from app.models.quartier_proximity import QuartierProximity

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
    user.city_id = city.id

    q_tokoin = Quartier(
        id=uuid4(), name="Tokoin", city_id=city.id,
        latitude=6.158, longitude=1.2115,
    )
    q_be = Quartier(
        id=uuid4(), name="Bè", city_id=city.id,
        latitude=6.134, longitude=1.221,
    )
    q_agoe = Quartier(
        id=uuid4(), name="Agoè", city_id=city.id,
        latitude=6.1995, longitude=1.189,
    )
    db_session.add_all([q_tokoin, q_be, q_agoe])
    await db_session.flush()

    # Proximité : respecter la contrainte quartier_a_id < quartier_b_id
    ids = sorted([q_tokoin.id, q_be.id], key=str)
    db_session.add(
        QuartierProximity(
            quartier_a_id=ids[0],
            quartier_b_id=ids[1],
            proximity_score=0.82,
            distance_km=2.1,
        )
    )
    ids2 = sorted([q_tokoin.id, q_agoe.id], key=str)
    db_session.add(
        QuartierProximity(
            quartier_a_id=ids2[0],
            quartier_b_id=ids2[1],
            proximity_score=0.33,
            distance_km=7.8,
        )
    )
    await db_session.commit()
    await db_session.refresh(user)
    return city, q_tokoin, q_be, q_agoe


async def test_list_quartiers_by_city(
    client, auth_headers, db_session, test_user
):
    _, _, _, _ = await _seed_city_with_quartiers(db_session, test_user)
    city_id = test_user.city_id
    resp = await client.get(
        f"/quartiers?city_id={city_id}", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    names = [q["name"] for q in resp.json()]
    assert "Tokoin" in names and "Bè" in names and "Agoè" in names


async def test_add_quartier_success(client, auth_headers, db_session, test_user):
    _, tokoin, _, _ = await _seed_city_with_quartiers(db_session, test_user)
    resp = await client.post(
        "/quartiers/me",
        json={
            "quartier_id": str(tokoin.id),
            "relation_type": "lives",
            "is_primary": True,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["relation_type"] == "lives"
    assert body["is_primary"] is True
    assert body["quartier"]["name"] == "Tokoin"


async def test_add_quartier_wrong_city(
    client, auth_headers, db_session, test_user
):
    await _seed_city_with_quartiers(db_session, test_user)

    # Quartier d'une autre ville
    from app.models.city import City
    from app.models.quartier import Quartier

    other_city = City(
        id=uuid4(),
        name="Abidjan",
        country_code="CI",
        country_name="Côte d'Ivoire",
        timezone="Africa/Abidjan",
        currency_code="XOF",
        premium_price_monthly=5000,
        premium_price_weekly=1500,
        phase="launch",
    )
    db_session.add(other_city)
    await db_session.flush()
    q_other = Quartier(
        id=uuid4(), name="Cocody", city_id=other_city.id,
        latitude=5.35, longitude=-3.98,
    )
    db_session.add(q_other)
    await db_session.commit()

    resp = await client.post(
        "/quartiers/me",
        json={"quartier_id": str(q_other.id), "relation_type": "hangs"},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "quartier_not_in_city"


async def test_add_quartier_duplicate_same_type(
    client, auth_headers, db_session, test_user
):
    _, tokoin, _, _ = await _seed_city_with_quartiers(db_session, test_user)
    r1 = await client.post(
        "/quartiers/me",
        json={"quartier_id": str(tokoin.id), "relation_type": "lives"},
        headers=auth_headers,
    )
    assert r1.status_code == 201
    r2 = await client.post(
        "/quartiers/me",
        json={"quartier_id": str(tokoin.id), "relation_type": "lives"},
        headers=auth_headers,
    )
    assert r2.status_code == 400
    assert "duplicate_quartier_relation" in r2.json()["detail"]


async def test_add_quartier_max_interested_free(
    client, auth_headers, db_session, test_user
):
    """Free user : max 3 `interested`."""
    city, tokoin, be, agoe = await _seed_city_with_quartiers(
        db_session, test_user
    )

    # Crée un 4e quartier dans la ville
    from app.models.quartier import Quartier

    q_dji = Quartier(
        id=uuid4(), name="Djidjolé", city_id=city.id,
        latitude=6.167, longitude=1.1955,
    )
    db_session.add(q_dji)
    await db_session.commit()

    for q in (tokoin, be, agoe):
        r = await client.post(
            "/quartiers/me",
            json={"quartier_id": str(q.id), "relation_type": "interested"},
            headers=auth_headers,
        )
        assert r.status_code == 201, r.text

    # La 4e dépasse la limite
    r = await client.post(
        "/quartiers/me",
        json={"quartier_id": str(q_dji.id), "relation_type": "interested"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "max_quartiers_reached" in r.json()["detail"]


async def test_add_quartier_premium_lifts_interested_limit(
    client, auth_headers, db_session, test_user
):
    city, tokoin, be, agoe = await _seed_city_with_quartiers(
        db_session, test_user
    )
    from app.models.quartier import Quartier

    q_dji = Quartier(
        id=uuid4(), name="Djidjolé", city_id=city.id,
        latitude=6.167, longitude=1.1955,
    )
    db_session.add(q_dji)
    test_user.is_premium = True
    await db_session.commit()

    for q in (tokoin, be, agoe, q_dji):
        r = await client.post(
            "/quartiers/me",
            json={"quartier_id": str(q.id), "relation_type": "interested"},
            headers=auth_headers,
        )
        assert r.status_code == 201, r.text


async def test_get_my_quartiers_grouped(
    client, auth_headers, db_session, test_user
):
    _, tokoin, be, _ = await _seed_city_with_quartiers(db_session, test_user)

    await client.post(
        "/quartiers/me",
        json={"quartier_id": str(tokoin.id), "relation_type": "lives", "is_primary": True},
        headers=auth_headers,
    )
    await client.post(
        "/quartiers/me",
        json={"quartier_id": str(be.id), "relation_type": "hangs"},
        headers=auth_headers,
    )

    resp = await client.get("/quartiers/me", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["lives"]) == 1 and body["lives"][0]["name"] == "Tokoin"
    assert len(body["hangs"]) == 1 and body["hangs"][0]["name"] == "Bè"
    assert body["limits"]["lives"]["max"] == 2
    assert body["limits"]["interested"]["max"] == 3
    assert body["limits"]["interested"]["max_premium"] == 6


async def test_remove_quartier(
    client, auth_headers, db_session, test_user
):
    _, tokoin, _, _ = await _seed_city_with_quartiers(db_session, test_user)
    await client.post(
        "/quartiers/me",
        json={"quartier_id": str(tokoin.id), "relation_type": "lives"},
        headers=auth_headers,
    )
    resp = await client.delete(
        f"/quartiers/me/{tokoin.id}?relation_type=lives",
        headers=auth_headers,
    )
    assert resp.status_code == 204, resp.text


async def test_nearby_quartiers(client, auth_headers, db_session, test_user):
    _, tokoin, _, _ = await _seed_city_with_quartiers(db_session, test_user)
    resp = await client.get(
        f"/quartiers/{tokoin.id}/nearby", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["quartier"]["name"] == "Tokoin"
    # Bè doit être le plus proche (0.82)
    assert body["nearby"][0]["name"] == "Bè"
    assert body["nearby"][0]["proximity"] > body["nearby"][-1]["proximity"]


async def test_add_quartier_advances_onboarding(
    client, auth_headers, db_session, test_user
):
    """1 quartier 'lives' satisfait l'étape QUARTIERS."""
    _, tokoin, _, _ = await _seed_city_with_quartiers(db_session, test_user)
    r = await client.post(
        "/quartiers/me",
        json={"quartier_id": str(tokoin.id), "relation_type": "lives", "is_primary": True},
        headers=auth_headers,
    )
    assert r.status_code == 201
    await db_session.refresh(test_user)
    # user n'a pas encore de profile → next étape bloquante avant QUARTIERS
    # reste basic_info / selfie_verification, pas un check strict ici.
    # On valide juste que QUARTIERS est maintenant considéré 'done'.
    from app.core.onboarding import OnboardingStep, is_step_done

    assert is_step_done(OnboardingStep.QUARTIERS, test_user, test_user.profile)
