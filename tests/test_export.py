from __future__ import annotations

"""Tests Export RGPD (§17, S13)."""

import zipfile
from uuid import uuid4

import pytest

from tests._feed_setup import headers_for, seed_ama_and_kofi

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_export_generates_valid_zip(client, db_session, redis_client):
    """Export → ZIP contenant profile.json, account.json, etc."""
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    resp = await client.get(
        "/profiles/me/export", headers=headers_for(ama)
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"

    # Verify ZIP contents
    import io

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert "profile.json" in names
    assert "account.json" in names
    assert "messages.json" in names
    assert "matches.json" in names
    assert "behavior.json" in names

    # Verify profile.json is valid JSON
    import json

    profile = json.loads(zf.read("profile.json"))
    assert profile["display_name"] == "Ama"
    assert profile["gender"] == "woman"

    # Verify account.json
    account = json.loads(zf.read("account.json"))
    assert account["city"] == "Lomé"
    assert account["is_premium"] is False


async def test_export_rate_limited_once_per_day(
    client, db_session, redis_client
):
    """2 exports rapides → le 2eme retourne 429."""
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    resp1 = await client.get(
        "/profiles/me/export", headers=headers_for(ama)
    )
    assert resp1.status_code == 200

    resp2 = await client.get(
        "/profiles/me/export", headers=headers_for(ama)
    )
    assert resp2.status_code == 429
    assert resp2.json()["error"] == "export_rate_limited"
