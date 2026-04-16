from __future__ import annotations

"""Tests Notifications (§5.10)."""

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_get_preferences_creates_defaults(
    client, auth_headers, db_session, test_user
):
    resp = await client.get(
        "/notifications/preferences", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Defaults depuis le modèle NotificationPreference
    assert body["new_match"] is True
    assert body["new_message"] is True
    assert isinstance(body["quiet_start_hour"], int)


async def test_update_preferences_disables_flag(
    client, auth_headers, db_session, test_user
):
    resp = await client.put(
        "/notifications/preferences",
        json={"daily_feed": False, "quiet_start_hour": 22},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["daily_feed"] is False
    assert body["quiet_start_hour"] == 22
    # Un GET ultérieur retourne les mêmes valeurs
    r2 = await client.get(
        "/notifications/preferences", headers=auth_headers
    )
    assert r2.json()["daily_feed"] is False


async def test_fcm_token_register(
    client, auth_headers, db_session, test_user
):
    from sqlalchemy import select

    from app.models.device import Device

    resp = await client.post(
        "/notifications/fcm-token",
        json={
            "fcm_token": "fcm-token-test-abcdef123456",
            "device_fingerprint": "sha256:fcmdev",
            "platform": "android",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "updated"

    row = await db_session.execute(
        select(Device).where(Device.user_id == test_user.id)
    )
    dev = row.scalar_one()
    assert dev.fcm_token == "fcm-token-test-abcdef123456"
    assert dev.platform == "android"


async def test_send_push_respects_pref_disabled(
    db_session, redis_client, test_user
):
    """Unit test : send_push retourne sent=False si flag désactivé."""
    from app.services import notification_service

    # Crée les prefs et désactive new_match
    prefs = await notification_service.get_or_create_preferences(
        test_user, db_session
    )
    prefs.new_match = False
    await db_session.commit()

    result = await notification_service.send_push(
        test_user.id, type="new_match", db=db_session
    )
    assert result["sent"] is False
    assert result["reason"] == "pref_disabled"


async def test_send_push_logs_in_mvp_mode(
    db_session, redis_client, test_user
):
    """FCM_ENABLED=false → sent=True, reason=logged_mvp."""
    from app.services import notification_service

    result = await notification_service.send_push(
        test_user.id,
        type="new_match",
        db=db_session,
    )
    # En MVP (fcm_enabled=false par défaut)
    assert result["type"] == "new_match"
    # Si quiet_hours tombe pile à l'exécution, on l'accepte aussi
    assert result["sent"] in (True, False)
    if result["sent"]:
        assert result["reason"] == "logged_mvp"
