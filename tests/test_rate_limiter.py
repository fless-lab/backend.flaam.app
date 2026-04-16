from __future__ import annotations

"""Tests rate limiter (§15, Session 9)."""

import pytest

from tests._feed_setup import headers_for, seed_ama_and_kofi

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_429_after_limit_with_retry_after(
    client, db_session, redis_client
):
    """
    /safety/report : 5/h. 6e requête → 429 avec Retry-After.
    """
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    payload = {
        "reported_user_id": str(kofi.id),
        "reason": "harassment",
    }
    # 5 premiers OK
    for i in range(5):
        r = await client.post(
            "/safety/report", json=payload, headers=headers_for(ama)
        )
        assert r.status_code == 201, f"call {i}: {r.text}"

    # 6e → 429
    r6 = await client.post(
        "/safety/report", json=payload, headers=headers_for(ama)
    )
    assert r6.status_code == 429
    assert "Retry-After" in r6.headers
    assert int(r6.headers["Retry-After"]) > 0
    assert "X-RateLimit-Limit" in r6.headers
    assert r6.headers["X-RateLimit-Limit"] == "5"
    assert r6.headers["X-RateLimit-Remaining"] == "0"


async def test_rate_limit_is_per_user(
    client, db_session, redis_client
):
    """
    Le rate limit est per-user : Ama atteint sa limite, Kofi peut
    encore poster.
    """
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    payload_ama = {
        "reported_user_id": str(kofi.id),
        "reason": "harassment",
    }
    for _ in range(5):
        r = await client.post(
            "/safety/report", json=payload_ama, headers=headers_for(ama)
        )
        assert r.status_code == 201
    r_block = await client.post(
        "/safety/report", json=payload_ama, headers=headers_for(ama)
    )
    assert r_block.status_code == 429

    # Kofi peut encore poster
    payload_kofi = {
        "reported_user_id": str(ama.id),
        "reason": "harassment",
    }
    r_kofi = await client.post(
        "/safety/report", json=payload_kofi, headers=headers_for(kofi)
    )
    assert r_kofi.status_code == 201
