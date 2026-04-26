from __future__ import annotations

"""Routes Messages (§5.8).

Dédup : le mécanisme PRINCIPAL est ``client_message_id`` dans le body
(ou form field pour les voice). Le header ``X-Idempotency-Key`` est un
fallback tolérant : s'il est présent, il doit correspondre au
``client_message_id`` du body (sinon 400).
"""

from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db, get_redis
from app.core.exceptions import AppException
from app.core.i18n import detect_lang
from app.core.rate_limiter import rate_limit
from app.models.user import User
from app.schemas.messages import (
    MeetupProposalBody,
    MeetupUpdateBody,
    MeetupResponseBody,
    MessageListResponse,
    MessageResponse,
    ReadReceiptBody,
    ReadReceiptResponse,
    SendMessageBody,
    UnreadCountResponse,
)
from app.services import chat_service

router = APIRouter(prefix="/messages", tags=["messages"])


def _ensure_header_matches(
    header_key: str | None, client_message_id: str
) -> None:
    if header_key is not None and header_key != client_message_id:
        raise AppException(
            status.HTTP_400_BAD_REQUEST, "idempotency_key_mismatch"
        )


@router.get("/{match_id}", response_model=MessageListResponse)
async def list_messages(
    match_id: UUID,
    request: Request,
    cursor: str | None = None,
    limit: int = 20,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    lang = detect_lang(request)
    return await chat_service.get_messages(
        match_id, user, cursor, limit, db, lang=lang
    )


@router.get("/{match_id}/unread-count", response_model=UnreadCountResponse)
async def unread_count(
    match_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    lang = detect_lang(request)
    return await chat_service.get_unread_count(match_id, user, db, lang=lang)


@router.post(
    "/{match_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(
            rate_limit(
                max_requests=60, window_seconds=60, name="messages_send"
            )
        )
    ],
)
async def send(
    match_id: UUID,
    body: SendMessageBody,
    request: Request,
    x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    _ensure_header_matches(x_idempotency_key, body.client_message_id)
    lang = detect_lang(request)
    return await chat_service.send_message(
        match_id=match_id,
        sender=user,
        content=body.content,
        client_message_id=body.client_message_id,
        db=db,
        redis=redis,
        lang=lang,
    )


@router.post(
    "/{match_id}/voice",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def send_voice_msg(
    match_id: UUID,
    request: Request,
    client_message_id: str = Form(..., min_length=1, max_length=64),
    file: UploadFile = File(...),
    x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    _ensure_header_matches(x_idempotency_key, client_message_id)
    lang = detect_lang(request)
    return await chat_service.send_voice(
        match_id=match_id,
        sender=user,
        upload=file,
        client_message_id=client_message_id,
        db=db,
        redis=redis,
        lang=lang,
    )


@router.post(
    "/{match_id}/meetup",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def send_meetup(
    match_id: UUID,
    body: MeetupProposalBody,
    request: Request,
    x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    _ensure_header_matches(x_idempotency_key, body.client_message_id)
    lang = detect_lang(request)
    return await chat_service.propose_meetup(
        match_id=match_id,
        sender=user,
        spot_id=body.spot_id,
        spot_name=body.spot_name,
        spot_lat=body.spot_lat,
        spot_lng=body.spot_lng,
        proposed_date=body.proposed_date,
        proposed_time=body.proposed_time,
        note=body.note,
        client_message_id=body.client_message_id,
        db=db,
        redis=redis,
        lang=lang,
    )


@router.patch("/{message_id}/meetup", response_model=MessageResponse)
async def respond_meetup_msg(
    message_id: UUID,
    body: MeetupResponseBody,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    lang = detect_lang(request)
    return await chat_service.respond_meetup(
        message_id=message_id,
        responder=user,
        action=body.action,
        counter_date=body.counter_date,
        counter_time=body.counter_time,
        db=db,
        lang=lang,
    )


@router.patch("/{message_id}/meetup/edit", response_model=MessageResponse)
async def edit_meetup_msg(
    message_id: UUID,
    body: MeetupUpdateBody,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    """Permet à l'expéditeur de modifier sa proposition meetup avant
    qu'elle soit acceptée/refusée. Reset le statut à 'proposed'."""
    lang = detect_lang(request)
    return await chat_service.update_meetup(
        message_id=message_id,
        sender=user,
        spot_id=body.spot_id,
        spot_name=body.spot_name,
        spot_lat=body.spot_lat,
        spot_lng=body.spot_lng,
        proposed_date=body.proposed_date,
        proposed_time=body.proposed_time,
        note=body.note,
        db=db,
        redis=redis,
        lang=lang,
    )


@router.patch("/{match_id}/read", response_model=ReadReceiptResponse)
async def mark_messages_read(
    match_id: UUID,
    body: ReadReceiptBody,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    lang = detect_lang(request)
    return await chat_service.mark_read(
        match_id=match_id,
        user=user,
        last_read_message_id=body.last_read_message_id,
        db=db,
        redis=redis,
        lang=lang,
    )


__all__ = ["router"]
