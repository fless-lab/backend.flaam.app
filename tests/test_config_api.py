from __future__ import annotations

"""Tests config API (§5.14, Session 9)."""

import pytest

from app.models.matching_config import MatchingConfig
from tests._feed_setup import headers_for, seed_ama_and_kofi

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_version_endpoint_no_auth(client, db_session, redis_client):
    """GET /config/version : pas d'auth requise."""
    r = await client.get("/config/version")
    assert r.status_code == 200
    body = r.json()
    assert "min_version" in body
    assert "current_version" in body
    assert "force_update" in body
    assert isinstance(body["force_update"], bool)


async def test_feature_flags_returns_defaults(
    client, db_session, redis_client
):
    """
    Sans override DB, les flags reflètent les MATCHING_DEFAULTS :
    - flag_targeted_likes_enabled = 0.0 → False
    - flag_reply_reminders_enabled = 1.0 → True
    """
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    r = await client.get(
        "/config/feature-flags", headers=headers_for(ama)
    )
    assert r.status_code == 200
    flags = r.json()["flags"]

    assert flags["flag_targeted_likes_enabled"] is False
    assert flags["flag_reply_reminders_enabled"] is True
    assert flags["flag_premium_enabled"] is True


async def test_feature_flags_reflects_db_override(
    client, db_session, redis_client
):
    """
    Un MatchingConfig flag_targeted_likes_enabled=1.0 rend le flag True.
    """
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    db_session.add(
        MatchingConfig(
            key="flag_targeted_likes_enabled",
            value=1.0,
            category="flags",
        )
    )
    await db_session.commit()

    r = await client.get(
        "/config/feature-flags", headers=headers_for(ama)
    )
    assert r.status_code == 200
    flags = r.json()["flags"]
    assert flags["flag_targeted_likes_enabled"] is True


async def test_feature_flags_requires_auth(
    client, db_session, redis_client
):
    r = await client.get("/config/feature-flags")
    assert r.status_code == 401
