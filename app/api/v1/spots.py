from __future__ import annotations

"""Routes Spots (§5.5)."""

from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db, get_redis
from app.core.rate_limiter import rate_limit
from app.models.user import User
from app.schemas.spots import (
    AddSpotBody,
    CheckinBody,
    CheckinResponse,
    SpotCategory,
    SpotDetailResponse,
    SpotOut,
    SpotVisibilityBody,
    SuggestSpotBody,
)
from app.services import feed_service, spot_service

router = APIRouter(prefix="/spots", tags=["spots"])


@router.get("", response_model=list[SpotOut])
async def search_spots(
    city_id: UUID = Query(...),
    category: SpotCategory | None = Query(default=None),
    q: str | None = Query(default=None, max_length=100),
    _me: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    spots = await spot_service.search_spots(city_id, db, category=category, query=q)
    return spot_service.serialize_spots(spots)


@router.get("/popular", response_model=list[SpotOut])
async def popular_spots(
    city_id: UUID = Query(...),
    _me: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    spots = await spot_service.get_popular_spots(city_id, db)
    return spot_service.serialize_spots(spots)


@router.post("/suggest", response_model=SpotOut, status_code=201)
async def suggest_spot(
    body: SuggestSpotBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    spot = await spot_service.suggest_spot(
        user,
        name=body.name,
        category=body.category,
        city_id=body.city_id,
        latitude=body.latitude,
        longitude=body.longitude,
        address=body.address,
        db=db,
    )
    return spot_service.serialize_spot(spot)


@router.get("/{spot_id}", response_model=SpotDetailResponse)
async def spot_detail(
    spot_id: UUID,
    _me: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await spot_service.get_spot_detail(spot_id, db)


@router.post("/me", response_model=SpotOut, status_code=201)
async def add_spot(
    body: AddSpotBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    us = await spot_service.add_spot(user, body.spot_id, db)
    if user.city_id:
        await feed_service.invalidate_city_feeds(user.city_id, db, redis)
    return spot_service.serialize_spot(us.spot)


@router.delete("/me/{spot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_spot(
    spot_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> Response:
    await spot_service.remove_spot(user, spot_id, db)
    if user.city_id:
        await feed_service.invalidate_city_feeds(user.city_id, db, redis)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/me/{spot_id}/visibility")
async def toggle_spot_visibility(
    spot_id: UUID,
    body: SpotVisibilityBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    us = await spot_service.toggle_spot_visibility(
        user, spot_id, body.is_visible, db
    )
    return {"spot_id": str(us.spot_id), "is_visible": us.is_visible}


@router.post(
    "/me/{spot_id}/checkin",
    response_model=CheckinResponse,
    dependencies=[Depends(rate_limit(max_requests=10, window_seconds=86400, name="spot_checkin"))],
)
async def checkin(
    spot_id: UUID,
    body: CheckinBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await spot_service.check_in(
        user, spot_id, body.latitude, body.longitude, db
    )


__all__ = ["router"]
