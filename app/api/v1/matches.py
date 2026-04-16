from __future__ import annotations

"""Routes Matches (§5.7)."""

from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db, get_redis
from app.core.i18n import detect_lang
from app.models.user import User
from app.schemas.matches import (
    LikesReceivedResponse,
    MatchDetailResponse,
    MatchListResponse,
    UnmatchResponse,
)
from app.services import feed_service, match_service

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("", response_model=MatchListResponse)
async def list_my_matches(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await match_service.list_matches(user, db)


# /likes-received doit être déclarée AVANT /{match_id} pour éviter que
# FastAPI ne matche "likes-received" comme un UUID.
@router.get("/likes-received", response_model=LikesReceivedResponse)
async def likes_received(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    return await feed_service.get_likes_received(
        user, db, redis, lang=detect_lang(request)
    )


@router.get("/{match_id}", response_model=MatchDetailResponse)
async def match_detail(
    match_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await match_service.get_match_detail(user, match_id, db)


@router.delete("/{match_id}", response_model=UnmatchResponse)
async def delete_match(
    match_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await match_service.unmatch(user, match_id, db)


__all__ = ["router"]
