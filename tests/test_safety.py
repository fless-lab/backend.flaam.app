from __future__ import annotations

"""Tests Safety (§5.11, Session 9)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.account_history import AccountHistory
from app.models.block import Block
from app.models.report import Report
from tests._feed_setup import headers_for, seed_ama_and_kofi

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ══════════════════════════════════════════════════════════════════════
# POST /safety/report
# ══════════════════════════════════════════════════════════════════════


async def test_report_user_creates_report(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    resp = await client.post(
        "/safety/report",
        json={
            "reported_user_id": str(kofi.id),
            "reason": "harassment",
            "description": "Messages insistants",
        },
        headers=headers_for(ama),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Status peut être pending, flagged_for_review ou auto_banned selon
    # le score de scam — dans ce scénario clean, attendu = pending.
    assert body["status"] in (
        "pending",
        "flagged_for_review",
        "auto_banned",
    )

    rows = await db_session.execute(
        select(Report).where(Report.reported_user_id == kofi.id)
    )
    reports = rows.scalars().all()
    assert len(reports) == 1
    assert reports[0].reason == "harassment"


async def test_report_self_forbidden(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    resp = await client.post(
        "/safety/report",
        json={
            "reported_user_id": str(ama.id),
            "reason": "other",
        },
        headers=headers_for(ama),
    )
    assert resp.status_code == 400


# ══════════════════════════════════════════════════════════════════════
# POST /safety/block + DELETE /safety/block/{id}
# ══════════════════════════════════════════════════════════════════════


async def test_block_user_creates_entry_and_updates_history(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    resp = await client.post(
        "/safety/block",
        json={"blocked_user_id": str(kofi.id)},
        headers=headers_for(ama),
    )
    assert resp.status_code == 201, resp.text

    # Block DB
    blocks = await db_session.execute(
        select(Block).where(
            Block.blocker_id == ama.id, Block.blocked_id == kofi.id
        )
    )
    assert blocks.scalar_one() is not None

    # AccountHistory du bloqué
    hist_row = await db_session.execute(
        select(AccountHistory).where(
            AccountHistory.phone_hash == kofi.phone_hash
        )
    )
    hist = hist_row.scalar_one_or_none()
    assert hist is not None
    assert hist.blocked_by_count == 1
    assert ama.phone_hash in hist.blocked_by_hashes


async def test_block_is_idempotent(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    for _ in range(2):
        resp = await client.post(
            "/safety/block",
            json={"blocked_user_id": str(kofi.id)},
            headers=headers_for(ama),
        )
        assert resp.status_code == 201

    blocks = await db_session.execute(
        select(Block).where(
            Block.blocker_id == ama.id, Block.blocked_id == kofi.id
        )
    )
    assert len(blocks.scalars().all()) == 1

    hist_row = await db_session.execute(
        select(AccountHistory).where(
            AccountHistory.phone_hash == kofi.phone_hash
        )
    )
    hist = hist_row.scalar_one()
    assert hist.blocked_by_count == 1  # pas de double incrément


async def test_unblock_user_removes_entry(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    await client.post(
        "/safety/block",
        json={"blocked_user_id": str(kofi.id)},
        headers=headers_for(ama),
    )
    resp = await client.delete(
        f"/safety/block/{kofi.id}", headers=headers_for(ama)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "unblocked"

    blocks = await db_session.execute(
        select(Block).where(
            Block.blocker_id == ama.id, Block.blocked_id == kofi.id
        )
    )
    assert blocks.scalar_one_or_none() is None


# ══════════════════════════════════════════════════════════════════════
# POST /safety/share-date (SMS mocké)
# ══════════════════════════════════════════════════════════════════════


async def test_share_date_calls_sms_service(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    mock_send = AsyncMock(
        return_value={"message_id": "sim-123", "provider": "simulated"}
    )
    with patch(
        "app.services.safety_service.sms_service.send_text", mock_send
    ):
        resp = await client.post(
            "/safety/share-date",
            json={
                "contact_phone": "+22899000000",
                "contact_name": "Maman",
                "partner_name": "Kofi",
                "meeting_place": "Café 21",
                "meeting_time": (
                    datetime.now(timezone.utc) + timedelta(hours=3)
                ).isoformat(),
            },
            headers=headers_for(ama),
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "sent"
    assert body["provider_message_id"] == "sim-123"
    mock_send.assert_called_once()
    # Vérifie le canal WhatsApp (primary pour les messages libres §35)
    assert mock_send.call_args.kwargs.get("channel") == "whatsapp"


# ══════════════════════════════════════════════════════════════════════
# Emergency timer
# ══════════════════════════════════════════════════════════════════════


async def test_emergency_timer_armed_sets_redis(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    resp = await client.post(
        "/safety/emergency",
        json={
            "contact_phone": "+22899000000",
            "contact_name": "Maman",
            "timer_hours": 3,
            "latitude": 6.135,
            "longitude": 1.221,
            "meeting_place": "Café 21",
        },
        headers=headers_for(ama),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "armed"

    # La clé Redis existe avec un TTL > 0
    key = f"safety:timer:{ama.id}"
    ttl = await redis_client.ttl(key)
    assert ttl > 0
    assert ttl <= 3 * 3600


async def test_emergency_timer_cancel_clears_redis(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    await client.post(
        "/safety/emergency",
        json={
            "contact_phone": "+22899000000",
            "timer_hours": 3,
        },
        headers=headers_for(ama),
    )
    resp = await client.post(
        "/safety/timer/cancel", headers=headers_for(ama)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    key = f"safety:timer:{ama.id}"
    assert await redis_client.get(key) is None


async def test_emergency_timer_cancel_when_none(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]
    resp = await client.post(
        "/safety/timer/cancel", headers=headers_for(ama)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_active_timer"
