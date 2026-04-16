from __future__ import annotations

"""Tests Spots (§5.5)."""

from uuid import uuid4

import pytest
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _seed_city_and_spot(
    db_session,
    user,
    *,
    name: str = "Café 21",
    category: str = "cafe",
    latitude: float = 6.1725,
    longitude: float = 1.2137,
    city_id=None,
):
    from app.models.city import City
    from app.models.spot import Spot

    if city_id is None:
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
        await db_session.flush()
    else:
        city = await db_session.get(City, city_id)

    spot = Spot(
        id=uuid4(),
        name=name,
        category=category,
        city_id=city.id,
        location=from_shape(Point(longitude, latitude), srid=4326),
        latitude=latitude,
        longitude=longitude,
        is_verified=True,
        is_active=True,
    )
    db_session.add(spot)
    await db_session.commit()
    await db_session.refresh(user)
    return city, spot


async def test_search_spots(client, auth_headers, db_session, test_user):
    city, spot = await _seed_city_and_spot(db_session, test_user)
    resp = await client.get(
        f"/spots?city_id={city.id}", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1 and body[0]["name"] == "Café 21"


async def test_search_spots_filter_category(
    client, auth_headers, db_session, test_user
):
    city, _ = await _seed_city_and_spot(db_session, test_user)
    # Ajoute un 2e spot d'une autre catégorie dans la même ville
    await _seed_city_and_spot(
        db_session, test_user, name="Salle Olympe", category="gym",
        latitude=6.135, longitude=1.2188, city_id=city.id,
    )
    resp = await client.get(
        f"/spots?city_id={city.id}&category=gym", headers=auth_headers
    )
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()]
    assert names == ["Salle Olympe"]


async def test_get_spot_detail(client, auth_headers, db_session, test_user):
    _, spot = await _seed_city_and_spot(db_session, test_user)
    resp = await client.get(f"/spots/{spot.id}", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Café 21"
    assert body["fidelity_distribution"] == {
        "declared": 0, "confirmed": 0, "regular": 0, "regular_plus": 0
    }


async def test_add_spot_success(client, auth_headers, db_session, test_user):
    _, spot = await _seed_city_and_spot(db_session, test_user)
    resp = await client.post(
        "/spots/me",
        json={"spot_id": str(spot.id)},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["id"] == str(spot.id)


async def test_add_spot_max_free(client, auth_headers, db_session, test_user):
    city, _ = await _seed_city_and_spot(db_session, test_user)
    from app.models.spot import Spot

    spots = []
    for i in range(5):
        s = Spot(
            id=uuid4(),
            name=f"Spot {i}",
            category="cafe",
            city_id=city.id,
            location=from_shape(Point(1.2 + i * 0.01, 6.1), srid=4326),
            latitude=6.1,
            longitude=1.2 + i * 0.01,
            is_active=True,
            is_verified=True,
        )
        db_session.add(s)
        spots.append(s)
    await db_session.commit()

    for s in spots:
        r = await client.post(
            "/spots/me", json={"spot_id": str(s.id)}, headers=auth_headers
        )
        assert r.status_code == 201, r.text

    # La 6e doit échouer
    from app.models.spot import Spot as SpotModel
    extra = SpotModel(
        id=uuid4(),
        name="Extra",
        category="cafe",
        city_id=city.id,
        location=from_shape(Point(1.3, 6.1), srid=4326),
        latitude=6.1, longitude=1.3,
        is_active=True, is_verified=True,
    )
    db_session.add(extra)
    await db_session.commit()

    r = await client.post(
        "/spots/me", json={"spot_id": str(extra.id)}, headers=auth_headers
    )
    assert r.status_code == 400
    assert "max_spots_reached" in r.json()["detail"]


async def test_checkin_success_levels_up(
    client, auth_headers, db_session, test_user
):
    _, spot = await _seed_city_and_spot(db_session, test_user)
    # Ajoute le spot d'abord pour avoir un UserSpot
    await client.post(
        "/spots/me", json={"spot_id": str(spot.id)}, headers=auth_headers
    )

    # 1er check-in → count=1, reste "declared" (seuil 2)
    r = await client.post(
        f"/spots/me/{spot.id}/checkin",
        json={"latitude": spot.latitude, "longitude": spot.longitude},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["checkin_count"] == 1
    assert r.json()["fidelity_level"] == "declared"
    assert r.json()["level_upgraded"] is False

    # 2e check-in → "confirmed"
    r = await client.post(
        f"/spots/me/{spot.id}/checkin",
        json={"latitude": spot.latitude, "longitude": spot.longitude},
        headers=auth_headers,
    )
    assert r.json()["fidelity_level"] == "confirmed"
    assert r.json()["level_upgraded"] is True


async def test_checkin_fidelity_progression_to_regular_plus(
    client, auth_headers, db_session, test_user
):
    _, spot = await _seed_city_and_spot(db_session, test_user)
    await client.post(
        "/spots/me", json={"spot_id": str(spot.id)}, headers=auth_headers
    )
    payload = {"latitude": spot.latitude, "longitude": spot.longitude}
    levels = []
    for _ in range(6):
        r = await client.post(
            f"/spots/me/{spot.id}/checkin", json=payload, headers=auth_headers
        )
        levels.append(r.json()["fidelity_level"])
    # count 1-5 : declared, confirmed, confirmed, regular, regular, regular_plus(6e)
    assert levels[0] == "declared"
    assert levels[1] == "confirmed"
    assert levels[3] == "regular"
    assert levels[5] == "regular_plus"


async def test_checkin_too_far(client, auth_headers, db_session, test_user):
    _, spot = await _seed_city_and_spot(db_session, test_user)
    await client.post(
        "/spots/me", json={"spot_id": str(spot.id)}, headers=auth_headers
    )
    # Coordonnées à ~5 km du spot
    r = await client.post(
        f"/spots/me/{spot.id}/checkin",
        json={"latitude": spot.latitude + 0.05, "longitude": spot.longitude},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "too_far" in r.json()["detail"]


async def test_popular_spots(client, auth_headers, db_session, test_user):
    city, spot = await _seed_city_and_spot(db_session, test_user)
    spot.total_checkins = 42
    await db_session.commit()
    resp = await client.get(
        f"/spots/popular?city_id={city.id}", headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()[0]["total_checkins"] == 42


async def test_toggle_spot_visibility(
    client, auth_headers, db_session, test_user
):
    _, spot = await _seed_city_and_spot(db_session, test_user)
    await client.post(
        "/spots/me", json={"spot_id": str(spot.id)}, headers=auth_headers
    )
    r = await client.patch(
        f"/spots/me/{spot.id}/visibility",
        json={"is_visible": False},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["is_visible"] is False
