from __future__ import annotations

"""
WebSocket chat (§5.8, §37 mobile spec).

Protocole minimal :

Client → serveur
  {"type": "ping"}
  {"type": "sync", "last_message_id": "uuid" | null}
  {"type": "sync", "match_id": "uuid", "last_message_id": "uuid"}
  {"type": "message", "match_id": "uuid", "content": "...", "client_message_id": "..."}
  {"type": "typing_start", "match_id": "uuid"}
  {"type": "typing_stop", "match_id": "uuid"}
  {"type": "read", "match_id": "uuid", "last_read_id": "uuid"}

Serveur → client
  {"type": "pong"}
  {"type": "sync_response", "messages": [...]}
  {"type": "new_message", "match_id": "...", "message": {...}}
  {"type": "ack", "client_message_id": "...", "message_id": "...", "status": "sent"}
  {"type": "typing_start"/"typing_stop", "match_id": "...", "from": "user_uuid"}
  {"type": "read", "match_id": "...", "last_read_id": "..."}
  {"type": "error", "detail": "...", "user_message_fr": "...", "user_message_en": "..."}

Codes de fermeture :
  4001 : JWT invalide / expiré.
  4002 : protocole invalide.

MVP : singleton in-process. Pour multi-worker (>1000 users concurrents
WS), migrer vers Redis pub/sub channel ``ws:broadcast:{user_id}``.
"""

import asyncio
import json
from datetime import date, time
from uuid import UUID

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.core.security import decode_token
from app.db.redis import redis_pool
from app.db.session import async_session
from app.models.match import Match
from app.models.user import User
from app.services import chat_service

log = structlog.get_logger()
settings = get_settings()

ONLINE_KEY = "ws:online:{user_id}"


# ══════════════════════════════════════════════════════════════════════
# Connection manager (singleton in-process)
# ══════════════════════════════════════════════════════════════════════


class ConnectionManager:
    """Registre des WebSockets actifs, indexé par user_id."""

    def __init__(self) -> None:
        self._sockets: dict[UUID, WebSocket] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            # Si un ancien socket existe, on le ferme proprement.
            old = self._sockets.get(user_id)
            if old is not None:
                try:
                    await old.close(code=4000, reason="replaced")
                except Exception:
                    pass
            self._sockets[user_id] = websocket

    async def disconnect(self, user_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            current = self._sockets.get(user_id)
            if current is websocket:
                del self._sockets[user_id]

    async def send_to(self, user_id: UUID, payload: dict) -> bool:
        """Envoie un JSON au user cible. Retourne True si delivered live."""
        ws = self._sockets.get(user_id)
        if ws is None:
            return False
        try:
            await ws.send_text(json.dumps(payload, default=str))
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("ws_send_failed", user_id=str(user_id), err=str(exc))
            return False

    def is_online(self, user_id: UUID) -> bool:
        return user_id in self._sockets


connection_manager = ConnectionManager()


# ══════════════════════════════════════════════════════════════════════
# Auth WS
# ══════════════════════════════════════════════════════════════════════


def _decode_access_to_user_id(token: str) -> UUID:
    """Décode un JWT d'access et retourne le user_id. Lève ValueError si KO."""
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise ValueError("wrong_token_type")
    try:
        return UUID(payload["sub"])
    except (KeyError, ValueError) as exc:  # noqa: PERF203
        raise ValueError("invalid_sub") from exc


async def _load_user(user_id: UUID, db: AsyncSession) -> User | None:
    user = await db.get(User, user_id)
    if (
        user is None
        or not user.is_active
        or user.is_banned
        or user.is_deleted
    ):
        return None
    return user


# ══════════════════════════════════════════════════════════════════════
# Router
# ══════════════════════════════════════════════════════════════════════


router = APIRouter()


@router.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="missing_token")
        return

    try:
        user_id = _decode_access_to_user_id(token)
    except (JWTError, ValueError):
        await websocket.close(code=4001, reason="invalid_token")
        return

    async with async_session() as db:
        user = await _load_user(user_id, db)
    if user is None:
        await websocket.close(code=4001, reason="user_unavailable")
        return

    await websocket.accept()
    await connection_manager.connect(user_id, websocket)

    redis_client = _get_redis_client()
    online_key = ONLINE_KEY.format(user_id=user_id)
    if redis_client is not None:
        try:
            await redis_client.set(
                online_key, "1", ex=settings.ws_online_ttl_seconds
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("ws_redis_online_set_failed", err=str(exc))

    heartbeat_task = asyncio.create_task(
        _heartbeat_refresh(user_id, redis_client, online_key)
    )

    try:
        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=settings.ws_idle_timeout_seconds,
                )
            except asyncio.TimeoutError:
                break

            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(
                    json.dumps({"type": "error", "detail": "invalid_json"})
                )
                continue

            await _dispatch(websocket, user, payload)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("ws_loop_error", user_id=str(user_id), err=str(exc))
    finally:
        heartbeat_task.cancel()
        await connection_manager.disconnect(user_id, websocket)
        if redis_client is not None:
            try:
                await redis_client.delete(online_key)
            except Exception:  # noqa: BLE001
                pass
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass


def _get_redis_client() -> aioredis.Redis | None:
    try:
        return redis_pool.client
    except RuntimeError:
        return None


async def _heartbeat_refresh(
    user_id: UUID, redis_client: aioredis.Redis | None, online_key: str
) -> None:
    if redis_client is None:
        return
    try:
        while True:
            await asyncio.sleep(settings.ws_heartbeat_seconds)
            try:
                await redis_client.expire(online_key, settings.ws_online_ttl_seconds)
            except Exception as exc:  # noqa: BLE001
                log.warning("ws_heartbeat_failed", err=str(exc))
    except asyncio.CancelledError:
        return


# ── Dispatcher ────────────────────────────────────────────────────────


async def _dispatch(websocket: WebSocket, user: User, payload: dict) -> None:
    t = payload.get("type")
    if t == "ping":
        await websocket.send_text(json.dumps({"type": "pong"}))
        return

    if t == "sync":
        await _handle_sync(websocket, user, payload)
        return

    if t == "message":
        await _handle_message(websocket, user, payload)
        return

    if t in ("typing_start", "typing_stop"):
        await _handle_typing(user, payload, t)
        return

    if t == "read":
        await _handle_read(user, payload)
        return

    await websocket.send_text(
        json.dumps({"type": "error", "detail": "unknown_type"})
    )


async def _handle_sync(websocket: WebSocket, user: User, payload: dict) -> None:
    last_id_raw = payload.get("last_message_id")
    match_id_raw = payload.get("match_id")
    last_id = None
    if last_id_raw:
        try:
            last_id = UUID(last_id_raw)
        except ValueError:
            await websocket.send_text(
                json.dumps({"type": "error", "detail": "invalid_last_message_id"})
            )
            return

    try:
        async with async_session() as db:
            if match_id_raw:
                try:
                    match_uuid = UUID(match_id_raw)
                except ValueError:
                    await websocket.send_text(
                        json.dumps({"type": "error", "detail": "invalid_match_id"})
                    )
                    return
                messages = await chat_service.sync_missed_messages(
                    match_uuid, user, last_id, db
                )
            else:
                messages = await chat_service.sync_all_user_matches(
                    user, last_id, db
                )
    except AppException as exc:
        await websocket.send_text(
            json.dumps({"type": "error", "detail": exc.detail})
        )
        return

    await websocket.send_text(
        json.dumps(
            {"type": "sync_response", "messages": messages}, default=str
        )
    )


async def _handle_message(websocket: WebSocket, user: User, payload: dict) -> None:
    match_id_raw = payload.get("match_id")
    content = payload.get("content")
    client_message_id = payload.get("client_message_id")

    if not match_id_raw or not content or not client_message_id:
        await websocket.send_text(
            json.dumps({"type": "error", "detail": "missing_fields"})
        )
        return

    try:
        match_uuid = UUID(match_id_raw)
    except ValueError:
        await websocket.send_text(
            json.dumps({"type": "error", "detail": "invalid_match_id"})
        )
        return

    redis_client = _get_redis_client()
    if redis_client is None:
        await websocket.send_text(
            json.dumps({"type": "error", "detail": "redis_unavailable"})
        )
        return

    try:
        async with async_session() as db:
            msg = await chat_service.send_message(
                match_id=match_uuid,
                sender=user,
                content=str(content),
                client_message_id=str(client_message_id),
                db=db,
                redis=redis_client,
            )
    except AppException as exc:
        await websocket.send_text(
            json.dumps(
                {
                    "type": "error",
                    "detail": exc.detail,
                    "user_message_fr": getattr(exc, "user_message_fr", None),
                    "user_message_en": getattr(exc, "user_message_en", None),
                }
            )
        )
        return

    await websocket.send_text(
        json.dumps(
            {
                "type": "ack",
                "client_message_id": client_message_id,
                "message_id": str(msg["id"]),
                "status": msg["status"],
            }
        )
    )


async def _handle_typing(user: User, payload: dict, event_type: str) -> None:
    match_id_raw = payload.get("match_id")
    if not match_id_raw:
        return
    try:
        match_uuid = UUID(match_id_raw)
    except ValueError:
        return
    # Relay sans DB, mais on doit vérifier que user est bien dans le match
    # pour éviter le spoofing vers n'importe qui.
    async with async_session() as db:
        match = await db.get(Match, match_uuid)
        if match is None or user.id not in (match.user_a_id, match.user_b_id):
            return
        partner_id = (
            match.user_b_id if match.user_a_id == user.id else match.user_a_id
        )
    await connection_manager.send_to(
        partner_id,
        {
            "type": event_type,
            "match_id": str(match_uuid),
            "from": str(user.id),
        },
    )


async def _handle_read(user: User, payload: dict) -> None:
    match_id_raw = payload.get("match_id")
    last_read_raw = payload.get("last_read_id")
    if not match_id_raw or not last_read_raw:
        return
    try:
        match_uuid = UUID(match_id_raw)
        last_read_uuid = UUID(last_read_raw)
    except ValueError:
        return
    redis_client = _get_redis_client()
    if redis_client is None:
        return
    try:
        async with async_session() as db:
            await chat_service.mark_read(
                match_id=match_uuid,
                user=user,
                last_read_message_id=last_read_uuid,
                db=db,
                redis=redis_client,
            )
    except AppException:
        return


__all__ = ["router", "connection_manager", "ONLINE_KEY"]
