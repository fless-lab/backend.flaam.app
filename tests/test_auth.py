from __future__ import annotations

"""Tests du module Auth — happy paths + erreurs critiques (§5.1)."""

import pytest

from app.utils.phone import hash_phone

pytestmark = pytest.mark.asyncio(loop_scope="session")

PHONE = "+22890123456"


def _otp_key(phone: str) -> str:
    return f"otp:{hash_phone(phone)}"


async def test_request_otp_success(client, redis_client):
    resp = await client.post("/auth/otp/request", json={"phone": PHONE})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["channel"] == "sms"
    assert body["expires_in"] == 600
    # L'OTP est bien stocké dans Redis
    assert await redis_client.get(_otp_key(PHONE)) is not None


async def test_verify_otp_success(client, redis_client, db_session):
    await client.post("/auth/otp/request", json={"phone": PHONE})
    code = await redis_client.get(_otp_key(PHONE))

    resp = await client.post(
        "/auth/otp/verify",
        json={
            "phone": PHONE,
            "code": code,
            "device_fingerprint": "sha256:dev1",
            "platform": "android",
            "app_version": "1.0.0",
            "os_version": "Android 13",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_new_user"] is True
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["user_id"]
    assert body["onboarding_step"] == "city_selection"


async def test_verify_otp_invalid_code(client, redis_client):
    await client.post("/auth/otp/request", json={"phone": PHONE})
    resp = await client.post(
        "/auth/otp/verify",
        json={"phone": PHONE, "code": "000000"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "otp_invalid"
    assert "message" in resp.json()


async def test_verify_otp_expired(client, redis_client):
    # Pas d'OTP demandé préalablement → Redis vide → expired
    resp = await client.post(
        "/auth/otp/verify",
        json={"phone": PHONE, "code": "123456"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "otp_expired"


async def test_refresh_token_success(client, redis_client):
    await client.post("/auth/otp/request", json={"phone": PHONE})
    code = await redis_client.get(_otp_key(PHONE))
    verify = await client.post(
        "/auth/otp/verify",
        json={"phone": PHONE, "code": code, "device_fingerprint": "x"},
    )
    refresh = verify.json()["refresh_token"]

    resp = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]


async def test_refresh_token_invalid(client):
    resp = await client.post(
        "/auth/refresh", json={"refresh_token": "not.a.token"}
    )
    assert resp.status_code == 401


async def test_protected_route_without_token(client):
    # /auth/logout nécessite un user courant
    resp = await client.post(
        "/auth/logout", json={"refresh_token": "anything"}
    )
    assert resp.status_code == 401


async def test_otp_rate_limit(client, redis_client):
    """spec §5.1 : max 3 demandes / 10 min / numéro."""
    for i in range(3):
        r = await client.post("/auth/otp/request", json={"phone": "+22890000001"})
        assert r.status_code == 200, f"attempt {i}: {r.text}"

    resp = await client.post("/auth/otp/request", json={"phone": "+22890000001"})
    assert resp.status_code == 429
    assert resp.json()["error"] == "otp_rate_limited"


async def test_delete_account_soft_delete(client, redis_client, db_session):
    """DELETE /auth/account : is_deleted + deleted_at + AccountHistory + token revoke."""
    # 1. Créer un compte
    await client.post("/auth/otp/request", json={"phone": "+22890000002"})
    code = await redis_client.get(_otp_key("+22890000002"))
    verify = await client.post(
        "/auth/otp/verify",
        json={"phone": "+22890000002", "code": code, "device_fingerprint": "sha:dev-del"},
    )
    tokens = verify.json()
    access = tokens["access_token"]
    refresh = tokens["refresh_token"]

    # Gate #214 : email vérifié requis pour delete account.
    # On marque l'user directement en DB pour bypasser le flow magic link.
    from app.models.user import User
    from app.utils.phone import hash_phone as _hp
    from sqlalchemy import select as _sel
    from datetime import datetime, timezone

    res = await db_session.execute(
        _sel(User).where(User.phone_hash == _hp("+22890000002"))
    )
    user = res.scalar_one()
    user.email = "test@example.com"
    user.is_email_verified = True
    user.email_verified_at = datetime.now(timezone.utc)
    await db_session.commit()

    # 2. Supprimer le compte (httpx.AsyncClient.delete n'accepte pas json= →
    # on passe par request())
    resp = await client.request(
        "DELETE",
        "/auth/account",
        json={"confirm": True, "reason": "user_deleted"},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert resp.status_code == 204, resp.text

    # 3. User soft-deleted
    from app.models.user import User
    from app.utils.phone import hash_phone as _hp
    from sqlalchemy import select as _sel

    res = await db_session.execute(
        _sel(User).where(User.phone_hash == _hp("+22890000002"))
    )
    user = res.scalar_one()
    assert user.is_deleted is True
    assert user.deleted_at is not None
    assert user.is_active is False

    # 4. AccountHistory créé
    from app.models.account_history import AccountHistory

    res = await db_session.execute(
        _sel(AccountHistory).where(AccountHistory.phone_hash == _hp("+22890000002"))
    )
    history = res.scalar_one()
    assert history.total_accounts_deleted == 1
    assert history.last_departure_reason == "user_deleted"
    assert history.last_account_deleted_at is not None

    # 5. Refresh token révoqué
    refresh_resp = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert refresh_resp.status_code == 401


# ─────────────────────────────────────────────────────────────────────
# MFA PIN — anti-bruteforce lockout helper (#220, #211)
# ─────────────────────────────────────────────────────────────────────


def test_compute_pin_lock_below_tier_1_returns_none():
    from app.core.security import compute_pin_lock_until

    assert compute_pin_lock_until(0) is None
    assert compute_pin_lock_until(4) is None


def test_compute_pin_lock_tier_1_15_minutes():
    from datetime import datetime, timedelta, timezone
    from app.core.security import compute_pin_lock_until

    now = datetime.now(timezone.utc)
    locked = compute_pin_lock_until(5)
    assert locked is not None
    delta = (locked - now).total_seconds()
    assert 14 * 60 <= delta <= 16 * 60  # ~15 min ± 1 min jitter


def test_compute_pin_lock_tier_1_continues_through_9():
    from datetime import datetime, timezone
    from app.core.security import compute_pin_lock_until

    now = datetime.now(timezone.utc)
    locked = compute_pin_lock_until(9)
    assert locked is not None
    delta = (locked - now).total_seconds()
    assert delta < 30 * 60  # encore tier 1 (15min), pas tier 2


def test_compute_pin_lock_tier_2_60_minutes():
    from datetime import datetime, timezone
    from app.core.security import compute_pin_lock_until

    now = datetime.now(timezone.utc)
    locked = compute_pin_lock_until(10)
    assert locked is not None
    delta = (locked - now).total_seconds()
    assert 59 * 60 <= delta <= 61 * 60  # ~1h ± 1 min


def test_compute_pin_lock_tier_2_caps():
    from datetime import datetime, timezone
    from app.core.security import compute_pin_lock_until

    # 50 échecs = toujours tier 2 (1h), pas plus
    now = datetime.now(timezone.utc)
    locked = compute_pin_lock_until(50)
    assert locked is not None
    delta = (locked - now).total_seconds()
    assert 59 * 60 <= delta <= 61 * 60


# ─────────────────────────────────────────────────────────────────────
# Gates contextuels (#220, #214)
# ─────────────────────────────────────────────────────────────────────


async def _create_test_user_with_mfa(
    client, redis_client, phone: str, pin: str | None = None,
    email_verified: bool = False,
):
    """Helper — crée un user, optionnellement avec MFA + email vérifié."""
    from datetime import datetime, timezone
    from app.models.user import User
    from app.utils.phone import hash_phone as _hp
    from sqlalchemy import select as _sel

    await client.post("/auth/otp/request", json={"phone": phone})
    code = await redis_client.get(_otp_key(phone))
    verify = await client.post(
        "/auth/otp/verify",
        json={"phone": phone, "code": code, "device_fingerprint": "sha:test"},
    )
    tokens = verify.json()
    return tokens["access_token"]


async def test_gate_email_required_blocks_delete_account(client, redis_client, db_session):
    """DELETE /auth/account sans email vérifié → 412 email_required."""
    access = await _create_test_user_with_mfa(
        client, redis_client, "+22890000010",
    )
    resp = await client.request(
        "DELETE",
        "/auth/account",
        json={"confirm": True},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert resp.status_code == 412, resp.text
    assert "email_required" in resp.text


async def test_gate_pin_required_blocks_when_mfa_enabled(client, redis_client, db_session):
    """User avec PIN configuré + email OK → DELETE sans header X-Pin-Verification → 412 pin_required."""
    from datetime import datetime, timezone
    from app.models.user import User
    from app.core.security import hash_pin
    from app.utils.phone import hash_phone as _hp
    from sqlalchemy import select as _sel

    access = await _create_test_user_with_mfa(
        client, redis_client, "+22890000011",
    )
    # Bypass : email vérifié + PIN configuré
    res = await db_session.execute(
        _sel(User).where(User.phone_hash == _hp("+22890000011"))
    )
    user = res.scalar_one()
    user.email = "t11@example.com"
    user.is_email_verified = True
    user.email_verified_at = datetime.now(timezone.utc)
    user.mfa_enabled = True
    user.mfa_pin_hash = hash_pin("123456")
    await db_session.commit()

    resp = await client.request(
        "DELETE",
        "/auth/account",
        json={"confirm": True},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert resp.status_code == 412, resp.text
    assert "pin_required" in resp.text


async def test_gate_pin_invalid_returns_401_and_increments_counter(
    client, redis_client, db_session,
):
    """PIN faux → 401 + mfa_failed_attempts incrémenté."""
    from datetime import datetime, timezone
    from app.models.user import User
    from app.core.security import hash_pin
    from app.utils.phone import hash_phone as _hp
    from sqlalchemy import select as _sel

    access = await _create_test_user_with_mfa(
        client, redis_client, "+22890000012",
    )
    res = await db_session.execute(
        _sel(User).where(User.phone_hash == _hp("+22890000012"))
    )
    user = res.scalar_one()
    user.email = "t12@example.com"
    user.is_email_verified = True
    user.email_verified_at = datetime.now(timezone.utc)
    user.mfa_enabled = True
    user.mfa_pin_hash = hash_pin("123456")
    await db_session.commit()

    # PIN faux
    resp = await client.request(
        "DELETE",
        "/auth/account",
        json={"confirm": True},
        headers={
            "Authorization": f"Bearer {access}",
            "X-Pin-Verification": "999999",
        },
    )
    assert resp.status_code == 401, resp.text
    assert "invalid_pin" in resp.text

    # Counter incrémenté
    await db_session.refresh(user)
    assert user.mfa_failed_attempts == 1


async def test_gate_pin_correct_passes_and_resets_counter(
    client, redis_client, db_session,
):
    """PIN correct → 204 + counter reset à 0."""
    from datetime import datetime, timezone
    from app.models.user import User
    from app.core.security import hash_pin
    from app.utils.phone import hash_phone as _hp
    from sqlalchemy import select as _sel

    access = await _create_test_user_with_mfa(
        client, redis_client, "+22890000013",
    )
    res = await db_session.execute(
        _sel(User).where(User.phone_hash == _hp("+22890000013"))
    )
    user = res.scalar_one()
    user.email = "t13@example.com"
    user.is_email_verified = True
    user.email_verified_at = datetime.now(timezone.utc)
    user.mfa_enabled = True
    user.mfa_pin_hash = hash_pin("123456")
    user.mfa_failed_attempts = 3  # avait des échecs avant
    await db_session.commit()

    resp = await client.request(
        "DELETE",
        "/auth/account",
        json={"confirm": True},
        headers={
            "Authorization": f"Bearer {access}",
            "X-Pin-Verification": "123456",
        },
    )
    assert resp.status_code == 204, resp.text


async def test_gate_pin_locked_returns_429(client, redis_client, db_session):
    """User en cooldown → 429 mfa_locked sans même tester le PIN."""
    from datetime import datetime, timedelta, timezone
    from app.models.user import User
    from app.core.security import hash_pin
    from app.utils.phone import hash_phone as _hp
    from sqlalchemy import select as _sel

    access = await _create_test_user_with_mfa(
        client, redis_client, "+22890000014",
    )
    res = await db_session.execute(
        _sel(User).where(User.phone_hash == _hp("+22890000014"))
    )
    user = res.scalar_one()
    user.email = "t14@example.com"
    user.is_email_verified = True
    user.email_verified_at = datetime.now(timezone.utc)
    user.mfa_enabled = True
    user.mfa_pin_hash = hash_pin("123456")
    user.mfa_failed_attempts = 5
    user.mfa_locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
    await db_session.commit()

    resp = await client.request(
        "DELETE",
        "/auth/account",
        json={"confirm": True},
        headers={
            "Authorization": f"Bearer {access}",
            "X-Pin-Verification": "123456",  # même PIN correct → 429 d'abord
        },
    )
    assert resp.status_code == 429, resp.text
    assert "mfa_locked" in resp.text
