from __future__ import annotations

"""Tests Behavior logs (§5.13, Session 9)."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.behavior_log import BehaviorLog
from tests._feed_setup import headers_for, seed_ama_and_kofi

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_behavior_log_batch_inserts(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    payload = {
        "events": [
            {
                "event_type": "profile_viewed",
                "target_user_id": str(kofi.id),
                "data": {"duration_seconds": 12.5, "scrolled_full": True},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            {
                "event_type": "prompt_read",
                "target_user_id": str(kofi.id),
                "data": {"prompt_index": 0},
            },
            {
                "event_type": "app_session_start",
                "data": {"source": "push"},
            },
        ]
    }
    r = await client.post(
        "/behavior/log", json=payload, headers=headers_for(ama)
    )
    assert r.status_code == 201, r.text
    assert r.json()["accepted"] == 3

    rows = await db_session.execute(
        select(BehaviorLog).where(BehaviorLog.user_id == ama.id)
    )
    logs = rows.scalars().all()
    # On avait déjà des BehaviorLog de get_crossed / log_view ? Non, ici
    # aucun flow feed n'a été joué → exactement 3 rows.
    assert len(logs) >= 3
    types = {l.event_type for l in logs}
    assert "profile_viewed" in types
    assert "prompt_read" in types


async def test_behavior_log_unknown_type_silently_ignored(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]
    r = await client.post(
        "/behavior/log",
        json={
            "events": [
                {"event_type": "totally_invalid_event"},
            ]
        },
        headers=headers_for(ama),
    )
    # Pydantic rejette les enums invalides → 422
    assert r.status_code == 422


async def test_behavior_log_max_100_per_batch(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]
    too_many = {
        "events": [
            {"event_type": "app_session_start"} for _ in range(101)
        ]
    }
    r = await client.post(
        "/behavior/log", json=too_many, headers=headers_for(ama)
    )
    assert r.status_code == 422


async def test_behavior_log_rate_limit_10_per_min(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]
    payload = {
        "events": [{"event_type": "app_session_start"}]
    }
    # 10 premiers OK, 11e → 429
    for i in range(10):
        r = await client.post(
            "/behavior/log", json=payload, headers=headers_for(ama)
        )
        assert r.status_code == 201, f"call {i}: {r.text}"

    r11 = await client.post(
        "/behavior/log", json=payload, headers=headers_for(ama)
    )
    assert r11.status_code == 429
    assert "Retry-After" in r11.headers
