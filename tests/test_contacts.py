from __future__ import annotations

"""Tests Contacts blacklist (§5.12, Session 9)."""

import hashlib

import pytest
from sqlalchemy import select

from app.models.contact_blacklist import ContactBlacklist
from tests._feed_setup import headers_for, seed_ama_and_kofi

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _hash_phone(num: str) -> str:
    return hashlib.sha256(num.encode()).hexdigest()


async def test_blacklist_import_and_list(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]
    hashes = [
        _hash_phone("+22899111111"),
        _hash_phone("+22899222222"),
    ]

    r = await client.post(
        "/contacts/blacklist",
        json={"phone_hashes": hashes},
        headers=headers_for(ama),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["imported"] == 2
    assert body["skipped"] == 0
    assert body["total"] == 2

    r2 = await client.get(
        "/contacts/blacklist", headers=headers_for(ama)
    )
    assert r2.status_code == 200
    listed = r2.json()
    assert listed["count"] == 2
    assert set(listed["phone_hashes"]) == set(hashes)


async def test_blacklist_reimport_skipped(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]
    h = [_hash_phone("+22899333333")]

    await client.post(
        "/contacts/blacklist",
        json={"phone_hashes": h},
        headers=headers_for(ama),
    )
    r = await client.post(
        "/contacts/blacklist",
        json={"phone_hashes": h},
        headers=headers_for(ama),
    )
    assert r.status_code == 201
    assert r.json()["imported"] == 0
    assert r.json()["skipped"] == 1


async def test_blacklist_delete(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]
    h = _hash_phone("+22899444444")

    await client.post(
        "/contacts/blacklist",
        json={"phone_hashes": [h]},
        headers=headers_for(ama),
    )
    r = await client.delete(
        f"/contacts/blacklist/{h}", headers=headers_for(ama)
    )
    assert r.status_code == 200
    assert r.json()["status"] == "deleted"

    # Deuxième delete → not_found
    r2 = await client.delete(
        f"/contacts/blacklist/{h}", headers=headers_for(ama)
    )
    assert r2.json()["status"] == "not_found"


async def test_blacklist_invalid_hash_rejected(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]
    r = await client.post(
        "/contacts/blacklist",
        json={"phone_hashes": ["notahash"]},
        headers=headers_for(ama),
    )
    assert r.status_code == 422  # pydantic validation error


async def test_blacklist_integration_excludes_from_feed(
    client, db_session, redis_client
):
    """Un contact blacklisté disparaît du feed (hard filters L1)."""
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    # Ama blackliste le numéro de Kofi
    r = await client.post(
        "/contacts/blacklist",
        json={"phone_hashes": [kofi.phone_hash]},
        headers=headers_for(ama),
    )
    assert r.status_code == 201

    # Feed de Ama ne doit PAS contenir Kofi.
    feed = await client.get("/feed", headers=headers_for(ama))
    assert feed.status_code == 200
    profile_ids = {p["user_id"] for p in feed.json()["profiles"]}
    assert str(kofi.id) not in profile_ids
