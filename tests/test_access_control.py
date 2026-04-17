from __future__ import annotations

"""
Tests d'acces croise (Session 14.5).

Verifie qu'un user ne peut PAS acceder aux ressources d'un autre user.
"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.emergency_contact import EmergencyContact
from app.models.match import Match
from app.models.message import Message
from app.models.photo import Photo
from app.models.profile import Profile
from app.models.user import User
from app.utils.phone import hash_phone

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ── Helpers ───────────────────────────────────────────────────────────


async def _make_user(
    db: AsyncSession, phone: str, display_name: str, *, city_id=None
) -> User:
    user = User(
        id=uuid4(),
        phone_hash=hash_phone(phone),
        phone_country_code="228",
        is_phone_verified=True,
        onboarding_step="completed",
        is_active=True,
        is_visible=True,
        city_id=city_id,
    )
    db.add(user)
    await db.flush()
    profile = Profile(
        user_id=user.id,
        display_name=display_name,
        birth_date=date(1998, 1, 1),
        gender="woman",
        seeking_gender="men",
        intention="serious",
        sector="tech",
        rhythm="early_bird",
        tags=[],
        languages=["fr"],
        prompts=[],
    )
    db.add(profile)
    for i in range(3):
        db.add(
            Photo(
                id=uuid4(),
                user_id=user.id,
                original_url=f"http://x/{user.id}/{i}.jpg",
                thumbnail_url=f"http://x/{user.id}/{i}_t.jpg",
                medium_url=f"http://x/{user.id}/{i}_m.jpg",
                display_order=i,
                content_hash=f"hash-{user.id}-{i}",
                width=800,
                height=1200,
                file_size_bytes=100_000,
                moderation_status="approved",
            )
        )
    await db.flush()
    await db.refresh(user)
    return user


def _headers(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


# ── Tests ─────────────────────────────────────────────────────────────


async def test_delete_other_users_photo_forbidden(client, db_session, redis_client):
    """User B ne peut pas supprimer la photo de User A."""
    user_a = await _make_user(db_session, "+22811110001", "Alice")
    user_b = await _make_user(db_session, "+22811110002", "Bob")
    await db_session.commit()

    # Get User A's first photo
    from sqlalchemy import select

    result = await db_session.execute(
        select(Photo).where(Photo.user_id == user_a.id).limit(1)
    )
    photo_a = result.scalar_one()

    resp = await client.delete(f"/photos/{photo_a.id}", headers=_headers(user_b))
    assert resp.status_code in (403, 404), f"Expected 403/404, got {resp.status_code}"


async def test_read_other_users_messages_forbidden(client, db_session, redis_client):
    """User B ne peut pas lire les messages du match entre A et C."""
    user_a = await _make_user(db_session, "+22811120001", "Alice")
    user_c = await _make_user(db_session, "+22811120003", "Chloe")
    user_b = await _make_user(db_session, "+22811120002", "Bob")

    match_ac = Match(
        id=uuid4(),
        user_a_id=user_a.id,
        user_b_id=user_c.id,
        status="matched",
        matched_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(match_ac)
    await db_session.commit()

    resp = await client.get(f"/messages/{match_ac.id}", headers=_headers(user_b))
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


async def test_send_message_to_other_match_forbidden(client, db_session, redis_client):
    """User B ne peut pas envoyer de message dans le match entre A et C."""
    user_a = await _make_user(db_session, "+22811130001", "Alice")
    user_c = await _make_user(db_session, "+22811130003", "Chloe")
    user_b = await _make_user(db_session, "+22811130002", "Bob")

    match_ac = Match(
        id=uuid4(),
        user_a_id=user_a.id,
        user_b_id=user_c.id,
        status="matched",
        matched_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(match_ac)
    await db_session.commit()

    resp = await client.post(
        f"/messages/{match_ac.id}",
        json={"content": "Salut", "client_message_id": "test-1"},
        headers=_headers(user_b),
    )
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


async def test_edit_other_users_emergency_contact_forbidden(
    client, db_session, redis_client
):
    """User B ne peut pas modifier le contact d'urgence de User A."""
    user_a = await _make_user(db_session, "+22811140001", "Alice")
    user_b = await _make_user(db_session, "+22811140002", "Bob")

    contact = EmergencyContact(
        id=uuid4(),
        user_id=user_a.id,
        name="Maman",
        phone="+22890000099",
        is_primary=True,
    )
    db_session.add(contact)
    await db_session.commit()

    resp = await client.put(
        f"/safety/contacts/{contact.id}",
        json={"name": "Hacked", "phone": "+22890000000"},
        headers=_headers(user_b),
    )
    assert resp.status_code in (403, 404), f"Expected 403/404, got {resp.status_code}"


async def test_delete_other_users_emergency_contact_forbidden(
    client, db_session, redis_client
):
    """User B ne peut pas supprimer le contact d'urgence de User A."""
    user_a = await _make_user(db_session, "+22811150001", "Alice")
    user_b = await _make_user(db_session, "+22811150002", "Bob")

    contact = EmergencyContact(
        id=uuid4(),
        user_id=user_a.id,
        name="Papa",
        phone="+22890000098",
        is_primary=True,
    )
    db_session.add(contact)
    await db_session.commit()

    resp = await client.delete(
        f"/safety/contacts/{contact.id}", headers=_headers(user_b)
    )
    assert resp.status_code in (403, 404), f"Expected 403/404, got {resp.status_code}"


async def test_unmatch_other_users_match_forbidden(client, db_session, redis_client):
    """User B ne peut pas unmatch le match entre A et C."""
    user_a = await _make_user(db_session, "+22811160001", "Alice")
    user_c = await _make_user(db_session, "+22811160003", "Chloe")
    user_b = await _make_user(db_session, "+22811160002", "Bob")

    match_ac = Match(
        id=uuid4(),
        user_a_id=user_a.id,
        user_b_id=user_c.id,
        status="matched",
        matched_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(match_ac)
    await db_session.commit()

    resp = await client.delete(f"/matches/{match_ac.id}", headers=_headers(user_b))
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


async def test_like_profile_not_in_feed(client, db_session, redis_client):
    """Liker un profil random (pas dans le feed) retourne 400 ou 404."""
    user_a = await _make_user(db_session, "+22811170001", "Alice")
    await db_session.commit()

    random_id = uuid4()
    resp = await client.post(
        f"/feed/{random_id}/like", json={}, headers=_headers(user_a)
    )
    assert resp.status_code in (400, 404, 429), f"Expected 400/404/429, got {resp.status_code}"


async def test_admin_endpoint_forbidden_for_normal_user(
    client, db_session, redis_client
):
    """Un user normal ne peut pas acceder aux endpoints admin."""
    user = await _make_user(db_session, "+22811180001", "Normal")
    await db_session.commit()

    resp = await client.get("/admin/reports", headers=_headers(user))
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"


async def test_other_users_profile_returns_limited_data(
    client, db_session, redis_client
):
    """GET /profiles/{user_id} ne retourne pas de donnees sensibles."""
    user_a = await _make_user(db_session, "+22811190001", "Alice")
    user_b = await _make_user(db_session, "+22811190002", "Bob")

    # Create a match so B can view A's profile
    match = Match(
        id=uuid4(),
        user_a_id=user_a.id,
        user_b_id=user_b.id,
        status="matched",
        matched_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(match)
    await db_session.commit()

    resp = await client.get(f"/profiles/{user_a.id}", headers=_headers(user_b))
    # May be 200 if match exists, or 403/404 if profile view is restricted
    if resp.status_code == 200:
        data = resp.json()
        sensitive_fields = [
            "phone", "phone_hash", "email", "recovery_email",
            "emergency_contacts", "device_fingerprint", "ip_address",
            "behavior_logs", "scam_score",
        ]
        for field in sensitive_fields:
            assert field not in data, f"Sensitive field '{field}' leaked in profile response"


async def test_export_only_available_on_me(client, db_session, redis_client):
    """L'export RGPD est seulement sur /profiles/me/export, pas /profiles/{id}/export."""
    user_a = await _make_user(db_session, "+22811200001", "Alice")
    user_b = await _make_user(db_session, "+22811200002", "Bob")
    await db_session.commit()

    # Try to access export for another user — route should not exist
    resp = await client.get(f"/profiles/{user_a.id}/export", headers=_headers(user_b))
    assert resp.status_code in (404, 405), f"Expected 404/405, got {resp.status_code}"
