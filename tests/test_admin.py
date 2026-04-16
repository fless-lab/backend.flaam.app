from __future__ import annotations

"""Tests Admin (§20, §22 — 22 endpoints).

Couvre le happy path des blocs principaux : reports / users / stats /
events / spots / photos moderation / matching-config / prompts /
batch / ambassadors / waitlist. Un test 403 vérifie l'enforcement
de `get_admin_user`.
"""

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from app.core.security import create_access_token
from app.models.city import City
from app.models.matching_config import MatchingConfig
from app.models.photo import Photo
from app.models.profile import Profile
from app.models.report import Report
from app.models.spot import Spot
from app.models.user import User
from app.models.waitlist_entry import WaitlistEntry
from app.utils.phone import hash_phone

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ── Helpers ─────────────────────────────────────────────────────────

async def _make_admin(db) -> tuple[User, dict]:
    u = User(
        id=uuid4(),
        phone_hash=hash_phone(f"+2289{uuid4().int % 10_000_000:07d}"),
        phone_country_code="228",
        is_phone_verified=True,
        is_admin=True,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    headers = {"Authorization": f"Bearer {create_access_token(u.id)}"}
    return u, headers


async def _make_city(db, name="Lomé") -> City:
    city = City(
        id=uuid4(),
        name=name,
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
    await db.commit()
    return city


async def _make_regular_user(db, city_id, display="Ama", gender="woman") -> User:
    u = User(
        id=uuid4(),
        phone_hash=hash_phone(f"+2289{uuid4().int % 10_000_000:07d}"),
        phone_country_code="228",
        is_phone_verified=True,
        city_id=city_id,
    )
    db.add(u)
    await db.flush()
    p = Profile(
        user_id=u.id,
        display_name=display,
        birth_date=date(1998, 6, 1),
        gender=gender,
        seeking_gender="men" if gender == "woman" else "women",
        intention="serious",
        sector="tech",
    )
    db.add(p)
    await db.commit()
    await db.refresh(u)
    return u


# ── Auth guard ───────────────────────────────────────────────────────

async def test_admin_routes_require_admin_flag(client, auth_headers):
    """Un user non-admin reçoit 403 sur toutes les routes /admin."""
    resp = await client.get("/admin/reports", headers=auth_headers)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "admin_required"


# ── Reports ──────────────────────────────────────────────────────────

async def test_admin_list_reports_empty(client, db_session):
    _, headers = await _make_admin(db_session)
    resp = await client.get("/admin/reports", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}


async def test_admin_act_on_report_resolve(client, db_session):
    admin, headers = await _make_admin(db_session)
    city = await _make_city(db_session)
    reporter = await _make_regular_user(db_session, city.id, "Reporter")
    reported = await _make_regular_user(db_session, city.id, "Reported", "man")
    r = Report(
        id=uuid4(),
        reporter_id=reporter.id,
        reported_user_id=reported.id,
        reason="spam",
        status="pending",
    )
    db_session.add(r)
    await db_session.commit()

    resp = await client.patch(
        f"/admin/reports/{r.id}",
        json={"action": "resolve", "note": "ok"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "resolved"
    assert body["resolution_note"] == "ok"
    assert body["resolved_by"] == str(admin.id)


# ── Users ────────────────────────────────────────────────────────────

async def test_admin_list_users_filters_by_status(client, db_session):
    _, headers = await _make_admin(db_session)
    city = await _make_city(db_session)
    active = await _make_regular_user(db_session, city.id, "Active")
    banned = await _make_regular_user(db_session, city.id, "Banned", "man")
    banned.is_banned = True
    banned.ban_reason = "spam"
    await db_session.commit()

    resp = await client.get("/admin/users?status=banned", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    ids = [u["id"] for u in body["items"]]
    assert str(banned.id) in ids
    assert str(active.id) not in ids


async def test_admin_ban_user(client, db_session):
    _, headers = await _make_admin(db_session)
    city = await _make_city(db_session)
    target = await _make_regular_user(db_session, city.id, "Target", "man")

    resp = await client.patch(
        f"/admin/users/{target.id}/ban",
        json={"reason": "repeated spam"},
        headers=headers,
    )
    assert resp.status_code == 200
    await db_session.refresh(target)
    assert target.is_banned is True
    assert target.ban_reason == "repeated spam"


async def test_admin_cannot_ban_another_admin(client, db_session):
    _, headers = await _make_admin(db_session)
    # Crée un deuxième admin
    other_admin = User(
        id=uuid4(),
        phone_hash=hash_phone(f"+2289{uuid4().int % 10_000_000:07d}"),
        phone_country_code="228",
        is_phone_verified=True,
        is_admin=True,
    )
    db_session.add(other_admin)
    await db_session.commit()

    resp = await client.patch(
        f"/admin/users/{other_admin.id}/ban",
        json={"reason": "abuse"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "cannot_ban_admin"


async def test_admin_change_user_gender_resets_selfie(client, db_session, redis_client):
    """PATCH /admin/users/{id}/gender force is_selfie_verified=False."""
    _, headers = await _make_admin(db_session)
    city = await _make_city(db_session)
    target = await _make_regular_user(db_session, city.id, "Tg", "woman")
    target.is_selfie_verified = True
    await db_session.commit()

    resp = await client.patch(
        f"/admin/users/{target.id}/gender",
        json={"new_gender": "non_binary", "reason": "user reported mismatch"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    await db_session.refresh(target)
    await db_session.refresh(target.profile)
    assert target.profile.gender == "non_binary"
    assert target.is_selfie_verified is False


# ── Stats ────────────────────────────────────────────────────────────

async def test_admin_dashboard_stats_zero_state(client, db_session):
    _, headers = await _make_admin(db_session)
    resp = await client.get("/admin/stats/dashboard", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_users_7d"] >= 0
    assert body["matches_per_day"] >= 0.0
    assert body["revenue_estimated_30d"] == 0
    assert isinstance(body["gender_ratio_by_city"], dict)


# ── Events ───────────────────────────────────────────────────────────

async def test_admin_event_create_and_delete(client, db_session):
    from geoalchemy2.shape import from_shape
    from shapely.geometry import Point

    _, headers = await _make_admin(db_session)
    city = await _make_city(db_session)
    spot = Spot(
        id=uuid4(),
        name="Café Admin",
        category="cafe",
        city_id=city.id,
        location=from_shape(Point(1.21, 6.15), srid=4326),
        latitude=6.15,
        longitude=1.21,
        is_verified=True,
        is_active=True,
    )
    db_session.add(spot)
    await db_session.commit()

    starts = datetime(2026, 12, 1, 18, 0, tzinfo=timezone.utc).isoformat()
    resp = await client.post(
        "/admin/events",
        json={
            "title": "Soirée test",
            "spot_id": str(spot.id),
            "city_id": str(city.id),
            "starts_at": starts,
            "category": "meetup",
            "status": "published",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    event_id = resp.json()["id"]

    # Delete → 204
    resp2 = await client.delete(f"/admin/events/{event_id}", headers=headers)
    assert resp2.status_code == 204


# ── Spots ────────────────────────────────────────────────────────────

async def test_admin_spot_validate_approve(client, db_session):
    from geoalchemy2.shape import from_shape
    from shapely.geometry import Point

    _, headers = await _make_admin(db_session)
    city = await _make_city(db_session)
    spot = Spot(
        id=uuid4(),
        name="À valider",
        category="restaurant",
        city_id=city.id,
        location=from_shape(Point(1.22, 6.13), srid=4326),
        latitude=6.13,
        longitude=1.22,
        is_verified=False,
        is_active=False,
    )
    db_session.add(spot)
    await db_session.commit()

    resp = await client.patch(
        f"/admin/spots/{spot.id}/validate",
        json={"action": "approve"},
        headers=headers,
    )
    assert resp.status_code == 200
    await db_session.refresh(spot)
    assert spot.is_verified is True
    assert spot.is_active is True


# ── Photos moderation ────────────────────────────────────────────────

async def test_admin_photo_moderate_approve(client, db_session):
    _, headers = await _make_admin(db_session)
    city = await _make_city(db_session)
    user = await _make_regular_user(db_session, city.id, "Ph")
    photo = Photo(
        id=uuid4(),
        user_id=user.id,
        original_url="http://x/a.webp",
        thumbnail_url="http://x/a_t.webp",
        medium_url="http://x/a_m.webp",
        display_order=0,
        content_hash="h" * 64,
        width=800,
        height=1200,
        file_size_bytes=1000,
        moderation_status="pending",
    )
    db_session.add(photo)
    await db_session.commit()

    resp = await client.patch(
        f"/admin/photos/{photo.id}/moderate",
        json={"action": "approve"},
        headers=headers,
    )
    assert resp.status_code == 200
    await db_session.refresh(photo)
    assert photo.moderation_status == "approved"


async def test_admin_photo_bulk_approve(client, db_session):
    _, headers = await _make_admin(db_session)
    city = await _make_city(db_session)
    user = await _make_regular_user(db_session, city.id, "Ph2")
    photos = []
    for i in range(3):
        p = Photo(
            id=uuid4(),
            user_id=user.id,
            original_url=f"http://x/{i}.webp",
            thumbnail_url=f"http://x/{i}_t.webp",
            medium_url=f"http://x/{i}_m.webp",
            display_order=i,
            content_hash="h" * 64,
            width=800,
            height=1200,
            file_size_bytes=1000,
            moderation_status="pending",
        )
        db_session.add(p)
        photos.append(p)
    await db_session.commit()

    resp = await client.post(
        "/admin/photos/bulk-approve",
        json={"photo_ids": [str(p.id) for p in photos]},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["approved"] == 3


async def test_admin_photo_stats_counts_by_status(client, db_session):
    _, headers = await _make_admin(db_session)
    resp = await client.get("/admin/photos/stats", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"pending", "approved", "rejected", "review"}


# ── Matching config ──────────────────────────────────────────────────

async def test_admin_matching_config_update(client, db_session, redis_client):
    _, headers = await _make_admin(db_session)
    cfg = MatchingConfig(
        key="geo_w_quartier",
        value=0.45,
        category="weights",
        min_value=0.0,
        max_value=1.0,
    )
    db_session.add(cfg)
    await db_session.commit()

    resp = await client.patch(
        "/admin/matching-config/geo_w_quartier",
        json={"value": 0.50},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["value"] == 0.50


# ── Prompts stats ────────────────────────────────────────────────────

async def test_admin_prompts_stats_ok(client, db_session):
    _, headers = await _make_admin(db_session)
    resp = await client.get("/admin/prompts/stats", headers=headers)
    assert resp.status_code == 200
    assert "items" in resp.json()


# ── Ambassadors ──────────────────────────────────────────────────────

async def test_admin_promote_ambassador(client, db_session):
    _, headers = await _make_admin(db_session)
    city = await _make_city(db_session)
    u = await _make_regular_user(db_session, city.id, "Amba")

    resp = await client.post(
        "/admin/ambassadors",
        json={"user_id": str(u.id), "code_count": 50},
        headers=headers,
    )
    assert resp.status_code == 201
    await db_session.refresh(u)
    assert u.is_ambassador is True


# ── Waitlist ─────────────────────────────────────────────────────────

async def test_admin_waitlist_stats_ok(client, db_session):
    _, headers = await _make_admin(db_session)
    city = await _make_city(db_session)
    # Deux users masculins sur la waitlist (position 1 & 2)
    for i in range(2):
        u = await _make_regular_user(db_session, city.id, f"W{i}", "man")
        db_session.add(
            WaitlistEntry(
                id=uuid4(),
                user_id=u.id,
                city_id=city.id,
                position=i + 1,
                status="waiting",
                gender="male",
            )
        )
    await db_session.commit()

    resp = await client.get("/admin/waitlist/stats", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_waiting"] >= 2
    assert body["min_position"] >= 1
