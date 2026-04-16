from __future__ import annotations

"""Tests Events, Check-in, Preregister (§5.9 + MàJ 8 Porte 3)."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

from app.utils.phone import hash_phone

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _seed_city_spot(db_session, user=None):
    from app.models.city import City
    from app.models.spot import Spot

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
    if user is not None:
        user.city_id = city.id
    await db_session.flush()

    spot = Spot(
        id=uuid4(),
        name="Rooftop Olympe",
        category="bar",
        city_id=city.id,
        location=from_shape(Point(1.2137, 6.1725), srid=4326),
        latitude=6.1725,
        longitude=1.2137,
        is_verified=True,
        is_active=True,
    )
    db_session.add(spot)
    await db_session.commit()
    return city, spot


async def _make_event(
    db_session,
    *,
    city,
    spot,
    status_: str = "published",
    max_attendees: int | None = 50,
    starts_in_days: int = 3,
    title: str = "Afterwork Rooftop",
    category: str = "afterwork",
):
    from app.models.event import Event

    ev = Event(
        id=uuid4(),
        title=title,
        description="Un afterwork sympa.",
        spot_id=spot.id,
        city_id=city.id,
        starts_at=datetime.now(timezone.utc) + timedelta(days=starts_in_days),
        ends_at=datetime.now(timezone.utc) + timedelta(days=starts_in_days, hours=3),
        category=category,
        max_attendees=max_attendees,
        current_attendees=0,
        is_approved=True,
        is_active=True,
        status=status_,
        slug=f"event-{uuid4().hex[:8]}",
    )
    db_session.add(ev)
    await db_session.commit()
    return ev


# ══════════════════════════════════════════════════════════════════════
# Events — list / register
# ══════════════════════════════════════════════════════════════════════


async def test_list_events_published_only(
    client, auth_headers, db_session, test_user
):
    city, spot = await _seed_city_spot(db_session, test_user)
    await _make_event(db_session, city=city, spot=spot, status_="published")
    await _make_event(
        db_session,
        city=city,
        spot=spot,
        status_="draft",
        title="Draft event",
    )

    resp = await client.get(f"/events?city_id={city.id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    titles = [e["title"] for e in body["events"]]
    assert "Afterwork Rooftop" in titles
    assert "Draft event" not in titles


async def test_register_event_success(
    client, auth_headers, db_session, test_user
):
    city, spot = await _seed_city_spot(db_session, test_user)
    ev = await _make_event(db_session, city=city, spot=spot)

    resp = await client.post(
        f"/events/{ev.id}/register", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "registered"
    assert body["current_attendees"] == 1


async def test_register_event_full_returns_409(
    client, auth_headers, db_session, test_user
):
    from app.models.event_registration import EventRegistration
    from app.models.user import User

    city, spot = await _seed_city_spot(db_session, test_user)
    ev = await _make_event(db_session, city=city, spot=spot, max_attendees=1)

    # Fill the event with another user
    other = User(
        phone_hash=hash_phone("+22800000011"),
        phone_country_code="228",
        city_id=city.id,
    )
    db_session.add(other)
    await db_session.flush()
    db_session.add(EventRegistration(event_id=ev.id, user_id=other.id))
    ev.current_attendees = 1
    ev.status = "full"
    await db_session.commit()

    resp = await client.post(
        f"/events/{ev.id}/register", headers=auth_headers
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "event_full"


async def test_unregister_event(
    client, auth_headers, db_session, test_user
):
    city, spot = await _seed_city_spot(db_session, test_user)
    ev = await _make_event(db_session, city=city, spot=spot)

    r1 = await client.post(f"/events/{ev.id}/register", headers=auth_headers)
    assert r1.status_code == 200
    r2 = await client.delete(
        f"/events/{ev.id}/register", headers=auth_headers
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "unregistered"
    assert r2.json()["current_attendees"] == 0


async def test_event_stats_anonymous_public(client, db_session, test_user):
    """Endpoint /events/{id}/stats est public — pas besoin d'auth."""
    city, spot = await _seed_city_spot(db_session, test_user)
    ev = await _make_event(db_session, city=city, spot=spot)

    resp = await client.get(f"/events/{ev.id}/stats")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["event_name"] == ev.title
    assert body["registered_count"] == 0
    assert body["checked_in_count"] == 0
    assert "quartier_breakdown" in body


async def test_event_detail_shows_registration(
    client, auth_headers, db_session, test_user
):
    city, spot = await _seed_city_spot(db_session, test_user)
    ev = await _make_event(db_session, city=city, spot=spot)
    await client.post(f"/events/{ev.id}/register", headers=auth_headers)

    resp = await client.get(f"/events/{ev.id}", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_registered"] is True
    assert body["registration_status"] == "registered"


# ══════════════════════════════════════════════════════════════════════
# Check-in QR
# ══════════════════════════════════════════════════════════════════════


async def test_checkin_valid_qr_success(
    client, auth_headers, db_session, test_user
):
    from app.core.security import sign_event_qr
    from app.models.event_registration import EventRegistration

    city, spot = await _seed_city_spot(db_session, test_user)
    ev = await _make_event(db_session, city=city, spot=spot)
    db_session.add(
        EventRegistration(
            event_id=ev.id, user_id=test_user.id, status="registered"
        )
    )
    await db_session.commit()

    qr = sign_event_qr(ev.id, test_user.id)
    resp = await client.post(
        f"/events/{ev.id}/checkin",
        json={"qr_code": qr},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "checked_in"
    assert body["attendees_count"] == 1


async def test_checkin_invalid_signature_403(
    client, auth_headers, db_session, test_user
):
    city, spot = await _seed_city_spot(db_session, test_user)
    ev = await _make_event(db_session, city=city, spot=spot)

    resp = await client.post(
        f"/events/{ev.id}/checkin",
        json={"qr_code": f"{ev.id}:{test_user.id}:deadbeefdeadbeef"},
        headers=auth_headers,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "invalid_qr_signature"


# ══════════════════════════════════════════════════════════════════════
# Event preregister (Porte 3)
# ══════════════════════════════════════════════════════════════════════


async def test_event_preregister_creates_ghost_user(
    client, redis_client, db_session, test_user
):
    """Demande OTP → verify → ghost user + QR."""
    from sqlalchemy import select

    from app.models.user import User

    city, spot = await _seed_city_spot(db_session, test_user)
    ev = await _make_event(db_session, city=city, spot=spot)

    phone = "+22890111222"
    r1 = await client.post(
        "/auth/event-preregister",
        json={"phone": phone, "event_id": str(ev.id)},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["otp_sent"] is True
    assert r1.json()["event_name"] == ev.title

    code = await redis_client.get(f"otp:{hash_phone(phone)}")
    assert code is not None

    r2 = await client.post(
        "/auth/event-preregister/verify",
        json={
            "phone": phone,
            "code": code,
            "event_id": str(ev.id),
            "first_name": "Mawuli",
        },
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["status"] == "registered"
    assert body["qr_code"] is not None
    assert body["qr_code"].startswith(str(ev.id))

    # Ghost user créé
    row = await db_session.execute(
        select(User).where(User.phone_hash == hash_phone(phone))
    )
    ghost = row.scalar_one()
    assert ghost.onboarding_step == "ghost"
    assert ghost.is_active is False
    assert ghost.first_name == "Mawuli"
    assert ghost.source_event_id == ev.id


async def test_ghost_user_otp_returns_prefilled(
    client, redis_client, db_session, test_user
):
    """Après preregister, OTP depuis l'app → is_ghost_conversion=true."""
    city, spot = await _seed_city_spot(db_session, test_user)
    ev = await _make_event(db_session, city=city, spot=spot)

    phone = "+22890333444"
    await client.post(
        "/auth/event-preregister",
        json={"phone": phone, "event_id": str(ev.id)},
    )
    code = await redis_client.get(f"otp:{hash_phone(phone)}")
    await client.post(
        "/auth/event-preregister/verify",
        json={
            "phone": phone,
            "code": code,
            "event_id": str(ev.id),
            "first_name": "Akoss",
        },
    )

    # Maintenant, OTP classique depuis l'app
    await client.post("/auth/otp/request", json={"phone": phone})
    code2 = await redis_client.get(f"otp:{hash_phone(phone)}")
    resp = await client.post(
        "/auth/otp/verify",
        json={
            "phone": phone,
            "code": code2,
            "device_fingerprint": "sha256:dev-ghost",
            "platform": "android",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_ghost_conversion"] is True
    assert body["ghost_data"] is not None
    assert body["ghost_data"]["first_name"] == "Akoss"
    assert body["ghost_data"]["event_name"] == ev.title
    # suggested_tags préremplis depuis la catégorie "afterwork"
    tags = body["ghost_data"]["suggested_tags"]
    assert "afterwork" in tags
