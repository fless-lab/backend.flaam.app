from __future__ import annotations

"""Tests Subscriptions + Paystack webhooks (§5.11, §16)."""

import hashlib
import hmac
import json
from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_city(db_session, user=None):
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
        phase="launch",
        is_active=True,
    )
    db_session.add(city)
    if user is not None:
        user.city_id = city.id
    await db_session.commit()
    return city


async def test_subscription_plans_returns_prices(
    client, auth_headers, db_session, test_user
):
    _city = await _make_city(db_session, test_user)
    # refresh pour que user.city soit chargé dans la route
    await db_session.refresh(test_user)

    resp = await client.get("/subscriptions/plans", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    plans = {p["code"]: p for p in body["plans"]}
    assert plans["weekly"]["amount"] == 1500
    assert plans["weekly"]["currency"] == "XOF"
    assert plans["monthly"]["amount"] == 5000


async def test_subscription_me_without_subscription(
    client, auth_headers, db_session, test_user
):
    resp = await client.get("/subscriptions/me", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_premium"] is False
    assert body.get("plan") is None


async def test_subscription_initialize_creates_payment(
    client, auth_headers, db_session, test_user
):
    from sqlalchemy import select

    from app.models.payment import Payment

    await _make_city(db_session, test_user)
    await db_session.refresh(test_user)

    resp = await client.post(
        "/subscriptions/initialize",
        json={"plan": "weekly", "payment_method": "momo"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan"] == "weekly"
    assert body["amount"] == 1500
    assert body["currency"] == "XOF"
    assert body["authorization_url"].startswith("http")
    reference = body["reference"]

    row = await db_session.execute(
        select(Payment).where(Payment.provider_reference == reference)
    )
    p = row.scalar_one()
    assert p.status == "pending"
    assert p.amount == 1500


async def test_webhook_simulate_activates_premium(
    client, auth_headers, db_session, test_user
):
    """Le flow complet dev : initialize → simulate webhook success."""
    from sqlalchemy import select

    from app.models.subscription import Subscription
    from app.models.user import User

    await _make_city(db_session, test_user)
    await db_session.refresh(test_user)

    init = await client.post(
        "/subscriptions/initialize",
        json={"plan": "weekly", "payment_method": "momo"},
        headers=auth_headers,
    )
    reference = init.json()["reference"]

    resp = await client.post(
        "/subscriptions/webhook/simulate",
        json={"reference": reference, "event": "charge.success"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "processed"

    # User.is_premium doit être True (rafraîchir depuis la DB)
    u = await db_session.get(User, test_user.id)
    await db_session.refresh(u)
    assert u.is_premium is True

    sub_row = await db_session.execute(
        select(Subscription).where(Subscription.user_id == test_user.id)
    )
    sub = sub_row.scalar_one()
    assert sub.is_active is True
    assert sub.plan == "weekly"


async def test_webhook_paystack_invalid_signature_401(
    client, db_session
):
    """Sans signature HMAC valide → 401."""
    payload = json.dumps(
        {
            "event": "charge.success",
            "data": {"reference": "fake", "amount": 1500},
        }
    )
    resp = await client.post(
        "/subscriptions/webhook/paystack",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Paystack-Signature": "invalid-signature",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_signature"


async def test_webhook_paystack_valid_signature_processes(
    client, auth_headers, db_session, test_user, monkeypatch
):
    """Payload signé HMAC-SHA512 → accepté et activation premium."""
    from app.core.config import get_settings

    # Force un webhook secret pour le test
    settings = get_settings()
    monkeypatch.setattr(settings, "paystack_webhook_secret", "test-secret")

    await _make_city(db_session, test_user)
    await db_session.refresh(test_user)

    init = await client.post(
        "/subscriptions/initialize",
        json={"plan": "weekly", "payment_method": "momo"},
        headers=auth_headers,
    )
    reference = init.json()["reference"]

    payload_dict = {
        "event": "charge.success",
        "data": {
            "reference": reference,
            "amount": 1500,
            "currency": "XOF",
            "status": "success",
            "metadata": {"user_id": str(test_user.id)},
        },
    }
    body = json.dumps(payload_dict).encode()
    sig = hmac.new(b"test-secret", body, hashlib.sha512).hexdigest()

    resp = await client.post(
        "/subscriptions/webhook/paystack",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Paystack-Signature": sig,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "processed"


async def test_payment_idempotent_on_replay(
    client, auth_headers, db_session, test_user
):
    """Même webhook renvoyé deux fois → pas de double activation."""
    from sqlalchemy import select

    from app.models.subscription import Subscription

    await _make_city(db_session, test_user)
    await db_session.refresh(test_user)

    init = await client.post(
        "/subscriptions/initialize",
        json={"plan": "monthly", "payment_method": "momo"},
        headers=auth_headers,
    )
    reference = init.json()["reference"]

    body = {"reference": reference, "event": "charge.success"}
    r1 = await client.post("/subscriptions/webhook/simulate", json=body)
    r2 = await client.post("/subscriptions/webhook/simulate", json=body)
    assert r1.status_code == 200
    assert r2.status_code == 200

    # Une seule subscription, expires_at pas doublée
    row = await db_session.execute(
        select(Subscription).where(Subscription.user_id == test_user.id)
    )
    subs = row.scalars().all()
    assert len(subs) == 1


async def test_subscription_cancel_sets_auto_renew_false(
    client, auth_headers, db_session, test_user
):
    await _make_city(db_session, test_user)
    await db_session.refresh(test_user)

    init = await client.post(
        "/subscriptions/initialize",
        json={"plan": "weekly", "payment_method": "momo"},
        headers=auth_headers,
    )
    reference = init.json()["reference"]
    await client.post(
        "/subscriptions/webhook/simulate",
        json={"reference": reference, "event": "charge.success"},
    )

    resp = await client.post("/subscriptions/cancel", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"

    me = await client.get("/subscriptions/me", headers=auth_headers)
    assert me.json()["is_auto_renew"] is False


async def test_webhook_simulate_404_when_disabled(
    client, monkeypatch
):
    """En prod (paystack_simulate=false), /webhook/simulate renvoie 404."""
    from app.api.v1 import subscriptions as sub_module

    monkeypatch.setattr(sub_module.settings, "paystack_simulate", False)

    resp = await client.post(
        "/subscriptions/webhook/simulate",
        json={"reference": "flaam_sub_any", "event": "charge.success"},
    )
    assert resp.status_code == 404
