from __future__ import annotations

"""
Tests WebSocket chat (§5.8, S7).

Utilise le TestClient synchrone Starlette (plus fiable que httpx-ws).
Les données créées sont nettoyées par le fixture ``sync_client``
(TRUNCATE en teardown).
"""

import asyncio
import json
import random
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.websockets import WebSocketDisconnect

from app.core.security import create_access_token
from app.db.session import async_session
from app.models.match import Match
from app.models.message import Message
from tests._feed_setup import (
    attach_quartier,
    make_user,
    seed_city_lome,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ══════════════════════════════════════════════════════════════════════
# Helpers — seed via une session DB réelle (pas le fixture db_session
# car celui-ci rollback et le WS ne verrait pas les données)
# ══════════════════════════════════════════════════════════════════════


async def _seed_ws_pair(engine):
    """Crée Lomé + Ama + Kofi + un Match(status=matched) et COMMIT."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix_a = "".join(str(random.randint(0, 9)) for _ in range(5))
    suffix_b = "".join(str(random.randint(0, 9)) for _ in range(5))
    async with factory() as db:
        base = await seed_city_lome(db)
        ama = await make_user(
            db,
            phone=f"+22890{suffix_a}",
            city_id=base["city"].id,
            display_name="Ama",
            gender="woman",
            seeking="men",
            birth_year=1999,
        )
        kofi = await make_user(
            db,
            phone=f"+22891{suffix_b}",
            city_id=base["city"].id,
            display_name="Kofi",
            gender="man",
            seeking="women",
            birth_year=1996,
        )
        await attach_quartier(db, ama, base["quartiers"]["tokoin"], "lives")
        await attach_quartier(db, kofi, base["quartiers"]["tokoin"], "lives")

        match = Match(
            id=uuid4(),
            user_a_id=ama.id,
            user_b_id=kofi.id,
            status="matched",
            matched_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        db.add(match)
        await db.commit()
        await db.refresh(ama)
        await db.refresh(kofi)
        await db.refresh(match)
        return ama, kofi, match


# ══════════════════════════════════════════════════════════════════════
# Auth
# ══════════════════════════════════════════════════════════════════════


async def test_websocket_connect_valid_jwt(sync_client, engine, redis_client):
    ama, _, _ = await _seed_ws_pair(engine)
    token = create_access_token(ama.id)

    with sync_client.websocket_connect(f"/ws/chat?token={token}") as ws:
        ws.send_text(json.dumps({"type": "ping"}))
        response = ws.receive_text()
        assert json.loads(response) == {"type": "pong"}


async def test_websocket_reject_invalid_jwt(sync_client, engine, redis_client):
    # Pas de données à seeder : connect échoue avant.
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with sync_client.websocket_connect("/ws/chat?token=not-a-valid-jwt") as ws:
            ws.receive_text()  # ne sera jamais reçu
    assert exc_info.value.code == 4001


async def test_websocket_reject_missing_token(sync_client, engine, redis_client):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with sync_client.websocket_connect("/ws/chat") as ws:
            ws.receive_text()
    assert exc_info.value.code == 4001


# ══════════════════════════════════════════════════════════════════════
# Sync
# ══════════════════════════════════════════════════════════════════════


async def test_websocket_sync_returns_missed_messages(
    sync_client, engine, redis_client
):
    ama, kofi, match = await _seed_ws_pair(engine)

    # Injecte 3 messages de Kofi à Ama directement en DB.
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        for i in range(3):
            db.add(
                Message(
                    id=uuid4(),
                    match_id=match.id,
                    sender_id=kofi.id,
                    message_type="text",
                    content=f"msg{i}",
                    status="sent",
                    client_message_id=f"sync-cmid-{i}",
                )
            )
        await db.commit()

    token = create_access_token(ama.id)
    with sync_client.websocket_connect(f"/ws/chat?token={token}") as ws:
        ws.send_text(
            json.dumps(
                {
                    "type": "sync",
                    "match_id": str(match.id),
                    "last_message_id": None,
                }
            )
        )
        response = json.loads(ws.receive_text())
        assert response["type"] == "sync_response"
        assert len(response["messages"]) == 3
        contents = [m["content"] for m in response["messages"]]
        assert contents == ["msg0", "msg1", "msg2"]


async def test_websocket_send_and_broadcast(sync_client, engine, redis_client):
    """Ama envoie un message via WS → Kofi le reçoit en live."""
    ama, kofi, match = await _seed_ws_pair(engine)
    token_ama = create_access_token(ama.id)
    token_kofi = create_access_token(kofi.id)

    with sync_client.websocket_connect(f"/ws/chat?token={token_kofi}") as ws_kofi:
        with sync_client.websocket_connect(f"/ws/chat?token={token_ama}") as ws_ama:
            ws_ama.send_text(
                json.dumps(
                    {
                        "type": "message",
                        "match_id": str(match.id),
                        "content": "Coucou Kofi",
                        "client_message_id": "ws-cmid-1",
                    }
                )
            )
            # Ama reçoit son ACK
            ack = json.loads(ws_ama.receive_text())
            assert ack["type"] == "ack"
            assert ack["client_message_id"] == "ws-cmid-1"
            assert ack["status"] in ("sent", "delivered")

            # Kofi reçoit le new_message
            msg = json.loads(ws_kofi.receive_text())
            assert msg["type"] == "new_message"
            assert msg["message"]["content"] == "Coucou Kofi"
            assert msg["match_id"] == str(match.id)


async def test_websocket_blocked_message_returns_error(
    sync_client, engine, redis_client
):
    ama, _, match = await _seed_ws_pair(engine)
    token = create_access_token(ama.id)

    with sync_client.websocket_connect(f"/ws/chat?token={token}") as ws:
        ws.send_text(
            json.dumps(
                {
                    "type": "message",
                    "match_id": str(match.id),
                    "content": "salope",
                    "client_message_id": "ws-block-1",
                }
            )
        )
        response = json.loads(ws.receive_text())
        assert response["type"] == "error"
        assert response["detail"] == "message_blocked_insult"
        assert response.get("message")
