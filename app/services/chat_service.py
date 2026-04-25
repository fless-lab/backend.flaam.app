from __future__ import annotations

"""
Chat service (spec §5.8).

Fonctions pures (pas de FastAPI). Les handlers REST + WebSocket les
appellent avec une AsyncSession fraîche.

Dédup : le ``client_message_id`` sert de clé idempotente. Un SET NX
Redis protège des retries rapides (TTL 24h) ; un index unique partiel
``(sender_id, client_message_id) WHERE client_message_id IS NOT NULL``
est la source de vérité DB.

Accès : chaque appel vérifie que ``user`` est participant du match.
Si non → 404 (ne pas divulguer l'existence).
"""

import json
import uuid as _uuid
from datetime import date, datetime, time, timezone
from pathlib import Path
from uuid import UUID

import redis.asyncio as aioredis
import structlog
from fastapi import UploadFile, status
from sqlalchemy import and_, asc, desc, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import FlaamError
from app.core.exceptions import AppException
from app.models.match import Match
from app.models.message import Message
from app.models.profile import Profile
from app.models.spot import Spot
from app.models.user import User
from app.services import moderation_service, notification_service

log = structlog.get_logger()
settings = get_settings()


# ── Clés Redis ────────────────────────────────────────────────────────

DEDUP_KEY = "chat:dedup:{sender_id}:{client_message_id}"
DEDUP_TTL_SECONDS = 24 * 3600

PAGE_DEFAULT = 20
PAGE_MAX = 50


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════


async def _get_active_match(
    match_id: UUID, user_id: UUID, db: AsyncSession, lang: str = "fr"
) -> Match:
    """Charge le match + vérifie que ``user_id`` est participant.

    Renvoie 404 si match inconnu, non matché, expiré ou si user non-participant.
    """
    match = await db.get(Match, match_id)
    if match is None:
        raise FlaamError("match_not_found", 404, lang)
    if user_id not in (match.user_a_id, match.user_b_id):
        raise FlaamError("match_not_found", 404, lang)
    if match.status != "matched":
        raise FlaamError("match_not_found", 404, lang)
    now = datetime.now(timezone.utc)
    if match.expires_at is not None and match.expires_at <= now:
        raise FlaamError("match_expired", 410, lang)
    return match


def _partner_id(match: Match, me_id: UUID) -> UUID:
    return match.user_b_id if match.user_a_id == me_id else match.user_a_id


def _msg_to_dict(msg: Message) -> dict:
    return {
        "id": msg.id,
        "match_id": msg.match_id,
        "sender_id": msg.sender_id,
        "content": msg.content,
        "message_type": msg.message_type,
        "status": msg.status,
        "created_at": msg.created_at,
        "client_message_id": msg.client_message_id,
        "media_url": msg.media_url,
        "media_duration_seconds": msg.media_duration_seconds,
        "meetup_data": msg.meetup_data,
    }


async def _is_first_message(match_id: UUID, db: AsyncSession) -> bool:
    exists_stmt = select(
        exists().where(Message.match_id == match_id)
    )
    found = await db.scalar(exists_stmt)
    return not bool(found)


# ══════════════════════════════════════════════════════════════════════
# GET /messages/{match_id}
# ══════════════════════════════════════════════════════════════════════


async def get_messages(
    match_id: UUID,
    user: User,
    cursor: str | None,
    limit: int,
    db: AsyncSession,
    lang: str = "fr",
) -> dict:
    """
    Pagination cursor-based, desc par created_at.

    - ``cursor`` = ISO datetime du message pivot (celui reçu en bas de
      page au chargement précédent).
    - Retourne les messages strictement plus anciens que ``cursor``.
    - ``next_cursor`` = created_at du plus vieux retourné si ``has_more``.
    """
    await _get_active_match(match_id, user.id, db, lang)

    limit = max(1, min(limit or PAGE_DEFAULT, PAGE_MAX))
    stmt = (
        select(Message)
        .where(Message.match_id == match_id)
        .order_by(desc(Message.created_at), desc(Message.id))
        .limit(limit + 1)
    )
    if cursor:
        try:
            cursor_dt = datetime.fromisoformat(cursor)
        except ValueError as e:
            raise AppException(
                status.HTTP_400_BAD_REQUEST, "invalid_cursor"
            ) from e
        stmt = stmt.where(Message.created_at < cursor_dt)

    rows = (await db.execute(stmt)).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = rows[-1].created_at.isoformat() if (rows and has_more) else None

    return {
        "messages": [_msg_to_dict(m) for m in rows],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


# ══════════════════════════════════════════════════════════════════════
# POST /messages/{match_id}
# ══════════════════════════════════════════════════════════════════════


async def send_message(
    match_id: UUID,
    sender: User,
    content: str,
    client_message_id: str,
    db: AsyncSession,
    redis: aioredis.Redis,
    lang: str = "fr",
) -> dict:
    match = await _get_active_match(match_id, sender.id, db, lang)

    # 1. Dédup Redis SETNX : si clé existe déjà, on renvoie le message existant.
    dedup_key = DEDUP_KEY.format(
        sender_id=sender.id, client_message_id=client_message_id
    )
    acquired = await redis.set(
        dedup_key, "pending", ex=DEDUP_TTL_SECONDS, nx=True
    )
    if not acquired:
        cached = await redis.get(dedup_key)
        if cached and cached != "pending":
            existing = await db.get(Message, UUID(cached))
            if existing is not None:
                return _msg_to_dict(existing)
        # Sinon fallback : lookup DB par (sender, client_message_id)
        row = await db.execute(
            select(Message).where(
                Message.sender_id == sender.id,
                Message.client_message_id == client_message_id,
            )
        )
        existing = row.scalar_one_or_none()
        if existing is not None:
            return _msg_to_dict(existing)
        # Clé posée mais DB pas encore flush (race) → on tombera sur
        # l'IntegrityError plus bas.

    # 2a. Anti-scam restriction des 3 premiers messages.
    # Bloque numéro/URL/argent tant que l'expéditeur n'a pas envoyé 3
    # messages dans cette conversation. Killer de scams en Afrique de
    # l'Ouest (cf. notes/scam_detect.txt + roadmap).
    from app.services import chat_restriction_service
    scam_pattern = await chat_restriction_service.check_message(
        match_id=match_id,
        sender_id=sender.id,
        content=content,
        db=db,
    )
    if scam_pattern is not None:
        await redis.delete(dedup_key)
        raise FlaamError(
            f"message_restricted_early:{scam_pattern}", 400, lang,
        )

    # 2b. Modération standard.
    is_first = await _is_first_message(match_id, db)
    mod = await moderation_service.check_message(
        content=content,
        sender_id=sender.id,
        match_id=match_id,
        is_first_message=is_first,
    )
    if mod.action == "block":
        # Libère le verrou : le client peut retry avec un autre contenu.
        await redis.delete(dedup_key)
        # Mapping moderation.reason → i18n key (§21 + principes produit).
        # Les deux seules raisons qui bloquent aujourd'hui : insult / suspicious_link*.
        if mod.reason == "insult":
            raise FlaamError("message_blocked_insult", 400, lang)
        if mod.reason and mod.reason.startswith("suspicious_link"):
            raise FlaamError("message_blocked_link", 400, lang)
        # Fallback défensif : action=block sans reason connue (ne devrait pas arriver).
        raise AppException(
            status.HTTP_400_BAD_REQUEST, mod.reason or "message_blocked"
        )

    # 3. Insert. On fixe created_at côté Python : l'ordre chronologique
    # doit être préservé même quand plusieurs messages sont insérés
    # dans la même transaction (server_default=func.now() partage la
    # transaction start time).
    now = datetime.now(timezone.utc)
    msg = Message(
        id=_uuid.uuid4(),
        match_id=match_id,
        sender_id=sender.id,
        message_type="text",
        content=content,
        status="sent",
        client_message_id=client_message_id,
        is_flagged=(mod.action == "flag_for_review"),
        flag_reason=mod.reason if mod.action in ("flag_for_review", "log") else None,
        created_at=now,
        updated_at=now,
    )
    db.add(msg)
    try:
        await db.flush()
    except IntegrityError:
        # Dédup via contrainte DB : on relit et on renvoie l'existant.
        await db.rollback()
        row = await db.execute(
            select(Message).where(
                Message.sender_id == sender.id,
                Message.client_message_id == client_message_id,
            )
        )
        existing = row.scalar_one_or_none()
        if existing is not None:
            return _msg_to_dict(existing)
        raise

    match.last_message_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(msg)

    # 4. Mémorise l'id dans Redis pour les retries futurs.
    await redis.set(dedup_key, str(msg.id), ex=DEDUP_TTL_SECONDS)

    # 5. Tente un push WS au partenaire (non bloquant).
    delivered_via_ws = await _try_push_live(match, sender.id, msg, db, redis)

    # 6. Si pas livre via WS (app fermee) → push FCM.
    await _maybe_push_new_message(match, sender, msg, delivered_via_ws, db)

    return _msg_to_dict(msg)


async def _try_push_live(
    match: Match,
    sender_id: UUID,
    msg: Message,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> bool:
    """Broadcast WS au partenaire si en ligne ; passe ``status=delivered``.

    Retourne True si le message a ete livre via WS (l'app du partenaire
    est ouverte) — sert a savoir si une push FCM est encore necessaire.
    """
    # Import local pour éviter le cycle chat_service ⇄ ws.chat.
    from app.ws.chat import connection_manager

    partner_id = _partner_id(match, sender_id)
    payload = {
        "type": "new_message",
        "match_id": str(match.id),
        "message": {
            "id": str(msg.id),
            "match_id": str(msg.match_id),
            "sender_id": str(msg.sender_id),
            "content": msg.content,
            "message_type": msg.message_type,
            "status": msg.status,
            "created_at": msg.created_at.isoformat(),
            "client_message_id": msg.client_message_id,
            "media_url": msg.media_url,
            "media_duration_seconds": msg.media_duration_seconds,
            "meetup_data": msg.meetup_data,
        },
    }
    delivered = await connection_manager.send_to(partner_id, payload)
    if delivered:
        msg.status = "delivered"
        await db.commit()
        await db.refresh(msg)
    return bool(delivered)


# ── Push helpers (FCM) ──────────────────────────────────────────────

_MESSAGE_PREVIEW_MAX = 40


async def _sender_display_name(sender: User, db: AsyncSession) -> str:
    """Retourne le display_name du sender, fallback "Quelqu'un" si absent."""
    if sender.profile is not None and sender.profile.display_name:
        return sender.profile.display_name
    row = await db.execute(
        select(Profile.display_name).where(Profile.user_id == sender.id)
    )
    name = row.scalar_one_or_none()
    return name or "Quelqu'un"


def _message_preview(msg: Message, lang: str = "fr") -> str:
    """Body preview court pour la push notification."""
    if msg.message_type == "voice":
        return "Nouveau message vocal" if lang == "fr" else "New voice message"
    if msg.message_type == "meetup":
        return "Proposition de rendez-vous" if lang == "fr" else "Meetup proposal"
    content = (msg.content or "").strip()
    if not content:
        return "Nouveau message" if lang == "fr" else "New message"
    if len(content) > _MESSAGE_PREVIEW_MAX:
        return content[: _MESSAGE_PREVIEW_MAX - 1].rstrip() + "…"
    return content


async def _maybe_push_new_message(
    match: Match,
    sender: User,
    msg: Message,
    delivered_via_ws: bool,
    db: AsyncSession,
) -> None:
    """Envoie une push FCM au partenaire si pas livre via WS.

    Silent fail : un probleme de notif ne doit jamais casser l'envoi.
    """
    if delivered_via_ws:
        return
    partner_id = _partner_id(match, sender.id)
    try:
        name = await _sender_display_name(sender, db)
        preview = _message_preview(msg, lang="fr")
        await notification_service.send_push(
            partner_id,
            type="notif_new_message",
            data={
                "name": name,
                "preview": preview,
                "match_id": str(match.id),
            },
            db=db,
        )
    except Exception as exc:  # noqa: BLE001
        log.info(
            "push_new_message_skipped",
            match_id=str(match.id),
            sender_id=str(sender.id),
            reason=str(exc),
        )


# ══════════════════════════════════════════════════════════════════════
# POST /messages/{match_id}/voice
# ══════════════════════════════════════════════════════════════════════


async def send_voice(
    match_id: UUID,
    sender: User,
    upload: UploadFile,
    client_message_id: str,
    db: AsyncSession,
    redis: aioredis.Redis,
    lang: str = "fr",
) -> dict:
    match = await _get_active_match(match_id, sender.id, db, lang)

    # Dédup (même logique que send_message)
    dedup_key = DEDUP_KEY.format(
        sender_id=sender.id, client_message_id=client_message_id
    )
    acquired = await redis.set(dedup_key, "pending", ex=DEDUP_TTL_SECONDS, nx=True)
    if not acquired:
        cached = await redis.get(dedup_key)
        if cached and cached != "pending":
            existing = await db.get(Message, UUID(cached))
            if existing is not None:
                return _msg_to_dict(existing)
        row = await db.execute(
            select(Message).where(
                Message.sender_id == sender.id,
                Message.client_message_id == client_message_id,
            )
        )
        existing = row.scalar_one_or_none()
        if existing is not None:
            return _msg_to_dict(existing)

    # Lecture + validation taille
    data = await upload.read()
    if len(data) == 0:
        await redis.delete(dedup_key)
        raise AppException(status.HTTP_400_BAD_REQUEST, "empty_voice")
    if len(data) > settings.voice_max_size_bytes:
        await redis.delete(dedup_key)
        raise AppException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "voice_too_large")

    # Persistance disque
    msg_id = _uuid.uuid4()
    voice_dir = Path(settings.storage_root) / str(sender.id) / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    path = voice_dir / f"{msg_id}.webm"
    path.write_bytes(data)

    public_url = f"/uploads/{sender.id}/voice/{msg_id}.webm"

    now = datetime.now(timezone.utc)
    msg = Message(
        id=msg_id,
        match_id=match_id,
        sender_id=sender.id,
        message_type="voice",
        media_url=public_url,
        media_duration_seconds=None,  # Durée renseignée par le client (futur)
        status="sent",
        client_message_id=client_message_id,
        created_at=now,
        updated_at=now,
    )
    db.add(msg)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        row = await db.execute(
            select(Message).where(
                Message.sender_id == sender.id,
                Message.client_message_id == client_message_id,
            )
        )
        existing = row.scalar_one_or_none()
        if existing is not None:
            return _msg_to_dict(existing)
        raise

    match.last_message_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(msg)

    await redis.set(dedup_key, str(msg.id), ex=DEDUP_TTL_SECONDS)
    delivered_via_ws = await _try_push_live(match, sender.id, msg, db, redis)
    await _maybe_push_new_message(match, sender, msg, delivered_via_ws, db)
    return _msg_to_dict(msg)


# ══════════════════════════════════════════════════════════════════════
# POST /messages/{match_id}/meetup
# ══════════════════════════════════════════════════════════════════════


async def propose_meetup(
    match_id: UUID,
    sender: User,
    spot_id: UUID,
    proposed_date: date,
    proposed_time: time,
    note: str | None,
    client_message_id: str,
    db: AsyncSession,
    redis: aioredis.Redis,
    lang: str = "fr",
) -> dict:
    match = await _get_active_match(match_id, sender.id, db, lang)

    spot = await db.get(Spot, spot_id)
    if spot is None or not spot.is_active:
        raise AppException(status.HTTP_404_NOT_FOUND, "spot_not_found")
    if sender.city_id is not None and spot.city_id != sender.city_id:
        raise AppException(status.HTTP_400_BAD_REQUEST, "spot_outside_city")

    # Dédup
    dedup_key = DEDUP_KEY.format(
        sender_id=sender.id, client_message_id=client_message_id
    )
    acquired = await redis.set(dedup_key, "pending", ex=DEDUP_TTL_SECONDS, nx=True)
    if not acquired:
        cached = await redis.get(dedup_key)
        if cached and cached != "pending":
            existing = await db.get(Message, UUID(cached))
            if existing is not None:
                return _msg_to_dict(existing)

    meetup_data = {
        "spot_id": str(spot_id),
        "spot_name": spot.name,
        "proposed_date": proposed_date.isoformat(),
        "proposed_time": proposed_time.isoformat(timespec="minutes"),
        "note": note,
        "status": "proposed",
        "counter_date": None,
        "counter_time": None,
    }

    now = datetime.now(timezone.utc)
    msg = Message(
        id=_uuid.uuid4(),
        match_id=match_id,
        sender_id=sender.id,
        message_type="meetup",
        content=note,
        meetup_data=meetup_data,
        status="sent",
        client_message_id=client_message_id,
        created_at=now,
        updated_at=now,
    )
    db.add(msg)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        row = await db.execute(
            select(Message).where(
                Message.sender_id == sender.id,
                Message.client_message_id == client_message_id,
            )
        )
        existing = row.scalar_one_or_none()
        if existing is not None:
            return _msg_to_dict(existing)
        raise

    match.last_message_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(msg)

    await redis.set(dedup_key, str(msg.id), ex=DEDUP_TTL_SECONDS)
    delivered_via_ws = await _try_push_live(match, sender.id, msg, db, redis)
    await _maybe_push_new_message(match, sender, msg, delivered_via_ws, db)
    return _msg_to_dict(msg)


# ══════════════════════════════════════════════════════════════════════
# PATCH /messages/{message_id}/meetup
# ══════════════════════════════════════════════════════════════════════


async def respond_meetup(
    message_id: UUID,
    responder: User,
    action: str,
    counter_date: date | None,
    counter_time: time | None,
    db: AsyncSession,
    lang: str = "fr",
) -> dict:
    msg = await db.get(Message, message_id)
    if msg is None or msg.message_type != "meetup":
        raise AppException(status.HTTP_404_NOT_FOUND, "meetup_not_found")

    _match = await _get_active_match(msg.match_id, responder.id, db, lang)
    # Le responder doit être le DESTINATAIRE de la proposition.
    if msg.sender_id == responder.id:
        raise AppException(status.HTTP_403_FORBIDDEN, "cannot_respond_own_meetup")

    if action not in ("accept", "modify", "refuse"):
        raise AppException(status.HTTP_400_BAD_REQUEST, "invalid_action")

    data = dict(msg.meetup_data or {})
    if action == "accept":
        data["status"] = "accepted"
    elif action == "refuse":
        data["status"] = "refused"
    else:  # modify
        if counter_date is None or counter_time is None:
            raise AppException(status.HTTP_400_BAD_REQUEST, "missing_counter_proposal")
        data["status"] = "countered"
        data["counter_date"] = counter_date.isoformat()
        data["counter_time"] = counter_time.isoformat(timespec="minutes")

    msg.meetup_data = data
    await db.commit()
    await db.refresh(msg)
    return _msg_to_dict(msg)


# ══════════════════════════════════════════════════════════════════════
# PATCH /messages/{match_id}/read
# ══════════════════════════════════════════════════════════════════════


async def mark_read(
    match_id: UUID,
    user: User,
    last_read_message_id: UUID,
    db: AsyncSession,
    redis: aioredis.Redis,
    lang: str = "fr",
) -> dict:
    await _get_active_match(match_id, user.id, db, lang)

    pivot = await db.get(Message, last_read_message_id)
    if pivot is None or pivot.match_id != match_id:
        raise AppException(status.HTTP_404_NOT_FOUND, "message_not_found")

    now = datetime.now(timezone.utc)
    # Marque tous les messages du partenaire ≤ pivot comme read.
    row = await db.execute(
        select(Message).where(
            Message.match_id == match_id,
            Message.sender_id != user.id,
            Message.created_at <= pivot.created_at,
            Message.status != "read",
        )
    )
    to_update = row.scalars().all()
    for m in to_update:
        m.status = "read"
        m.read_at = now
    await db.commit()

    # Broadcast au partenaire
    from app.ws.chat import connection_manager

    match = await db.get(Match, match_id)
    if match is not None:
        partner_id = _partner_id(match, user.id)
        await connection_manager.send_to(
            partner_id,
            {
                "type": "read",
                "match_id": str(match_id),
                "last_read_id": str(last_read_message_id),
            },
        )

    return {
        "match_id": match_id,
        "last_read_message_id": last_read_message_id,
        "updated_count": len(to_update),
    }


# ══════════════════════════════════════════════════════════════════════
# Unread count
# ══════════════════════════════════════════════════════════════════════


async def get_unread_count(
    match_id: UUID, user: User, db: AsyncSession, lang: str = "fr"
) -> dict:
    await _get_active_match(match_id, user.id, db, lang)
    row = await db.execute(
        select(func.count(Message.id)).where(
            Message.match_id == match_id,
            Message.sender_id != user.id,
            Message.status != "read",
        )
    )
    count = int(row.scalar_one() or 0)
    return {"match_id": match_id, "unread_count": count}


# ══════════════════════════════════════════════════════════════════════
# Sync (WebSocket)
# ══════════════════════════════════════════════════════════════════════


async def sync_missed_messages(
    match_id: UUID,
    user: User,
    last_message_id: UUID | None,
    db: AsyncSession,
    lang: str = "fr",
) -> list[dict]:
    """
    Retourne les messages du match créés strictement après ``last_message_id``,
    ordonnés asc.

    Si ``last_message_id`` est None → renvoie les 50 plus récents en ordre asc.
    """
    await _get_active_match(match_id, user.id, db, lang)

    stmt = select(Message).where(Message.match_id == match_id)
    if last_message_id is not None:
        pivot = await db.get(Message, last_message_id)
        if pivot is not None and pivot.match_id == match_id:
            stmt = stmt.where(Message.created_at > pivot.created_at)

    stmt = stmt.order_by(asc(Message.created_at)).limit(PAGE_MAX)
    rows = (await db.execute(stmt)).scalars().all()
    return [_msg_to_dict(m) for m in rows]


async def sync_all_user_matches(
    user: User,
    last_message_id: UUID | None,
    db: AsyncSession,
) -> list[dict]:
    """
    Sync cross-matches : renvoie tous les messages de l'utilisateur
    créés après ``last_message_id`` (tous matchs confondus).
    Utilisé par le client WS à la reconnexion sans contexte de match.
    """
    pivot_dt: datetime | None = None
    if last_message_id is not None:
        pivot = await db.get(Message, last_message_id)
        if pivot is not None:
            pivot_dt = pivot.created_at

    match_ids_row = await db.execute(
        select(Match.id).where(
            or_(Match.user_a_id == user.id, Match.user_b_id == user.id),
            Match.status == "matched",
        )
    )
    match_ids = [mid for (mid,) in match_ids_row.all()]
    if not match_ids:
        return []

    stmt = select(Message).where(Message.match_id.in_(match_ids))
    if pivot_dt is not None:
        stmt = stmt.where(Message.created_at > pivot_dt)
    stmt = stmt.order_by(asc(Message.created_at)).limit(PAGE_MAX * 2)
    rows = (await db.execute(stmt)).scalars().all()
    return [_msg_to_dict(m) for m in rows]


__all__ = [
    "get_messages",
    "send_message",
    "send_voice",
    "propose_meetup",
    "respond_meetup",
    "mark_read",
    "get_unread_count",
    "sync_missed_messages",
    "sync_all_user_matches",
]
