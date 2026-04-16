from __future__ import annotations

"""Tests X-Idempotency-Key middleware (§34, Session 9)."""

import pytest
from sqlalchemy import func, select

from app.models.block import Block
from tests._feed_setup import headers_for, seed_ama_and_kofi

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_retry_with_same_key_returns_cached_response(
    client, db_session, redis_client
):
    """
    POST /safety/block avec X-Idempotency-Key → retry renvoie EXACTEMENT
    le même body, et ne crée pas un 2e Block.
    """
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    headers = {
        **headers_for(ama),
        "X-Idempotency-Key": "test-key-block-001",
    }

    r1 = await client.post(
        "/safety/block",
        json={"blocked_user_id": str(kofi.id)},
        headers=headers,
    )
    assert r1.status_code == 201, r1.text
    body1 = r1.json()

    # Retry avec la même clé
    r2 = await client.post(
        "/safety/block",
        json={"blocked_user_id": str(kofi.id)},
        headers=headers,
    )
    assert r2.status_code == 201
    assert r2.json() == body1
    # Marker du replay
    assert r2.headers.get("X-Idempotent-Replay") == "true"

    # Un seul Block côté DB
    count_row = await db_session.execute(
        select(func.count(Block.id)).where(
            Block.blocker_id == ama.id, Block.blocked_id == kofi.id
        )
    )
    assert count_row.scalar_one() == 1


async def test_different_key_not_cached(
    client, db_session, redis_client
):
    """Une clé différente = nouvelle requête (pas de replay)."""
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    headers_a = {
        **headers_for(ama),
        "X-Idempotency-Key": "key-A",
    }
    r1 = await client.post(
        "/safety/block",
        json={"blocked_user_id": str(kofi.id)},
        headers=headers_a,
    )
    assert r1.status_code == 201
    assert r1.headers.get("X-Idempotent-Replay") != "true"

    headers_b = {
        **headers_for(ama),
        "X-Idempotency-Key": "key-B",
    }
    r2 = await client.post(
        "/safety/block",
        json={"blocked_user_id": str(kofi.id)},
        headers=headers_b,
    )
    # Pas un replay
    assert r2.headers.get("X-Idempotent-Replay") != "true"


async def test_no_header_means_no_caching(
    client, db_session, redis_client
):
    """Sans header X-Idempotency-Key, le middleware bypasse."""
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    r = await client.post(
        "/safety/block",
        json={"blocked_user_id": str(kofi.id)},
        headers=headers_for(ama),
    )
    assert r.status_code == 201
    assert r.headers.get("X-Idempotent-Replay") != "true"
