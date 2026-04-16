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
    assert "invalid_otp" in resp.json()["detail"]


async def test_verify_otp_expired(client, redis_client):
    # Pas d'OTP demandé préalablement → Redis vide → invalid
    resp = await client.post(
        "/auth/otp/verify",
        json={"phone": PHONE, "code": "123456"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"].startswith("invalid_otp")


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
    assert resp.json()["detail"].startswith("rate_limited")


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
