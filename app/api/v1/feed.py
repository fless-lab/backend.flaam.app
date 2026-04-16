from __future__ import annotations

"""Routes Feed (§5.6)."""

from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db, get_redis
from app.core.i18n import detect_lang
from app.core.rate_limiter import rate_limit
from app.models.user import User
from app.schemas.feed import (
    CrossedFeedResponse,
    DailyFeedResponse,
    LikeBody,
    LikeResponse,
    SkipBody,
    SkipResponse,
    ViewBody,
)
from app.services import feed_service

router = APIRouter(prefix="/feed", tags=["feed"])


@router.get("", response_model=DailyFeedResponse)
async def get_feed(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    return await feed_service.get_daily_feed(user, db, redis)


@router.get("/crossed", response_model=CrossedFeedResponse)
async def get_crossed(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    return await feed_service.get_crossed_feed(user, db, redis)


@router.post(
    "/{profile_id}/like",
    response_model=LikeResponse,
    dependencies=[
        Depends(
            rate_limit(
                max_requests=30, window_seconds=60, name="feed_like"
            )
        )
    ],
)
async def like(
    profile_id: UUID,
    body: LikeBody,
    request: Request,
    x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    return await feed_service.like_profile(
        user,
        profile_id,
        body.model_dump(),
        x_idempotency_key,
        db,
        redis,
        lang=detect_lang(request),
    )


@router.post("/{profile_id}/skip", response_model=SkipResponse)
async def skip(
    profile_id: UUID,
    body: SkipBody,
    x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    return await feed_service.skip_profile(
        user,
        profile_id,
        body.model_dump(),
        x_idempotency_key,
        db,
        redis,
    )


@router.post("/{profile_id}/view", status_code=status.HTTP_204_NO_CONTENT)
async def view(
    profile_id: UUID,
    body: ViewBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> Response:
    await feed_service.log_view(user, profile_id, body.model_dump(), db, redis)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
