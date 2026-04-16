from __future__ import annotations

"""Route Behavior logs (§5.13). 1 endpoint."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.core.rate_limiter import rate_limit
from app.models.user import User
from app.schemas.behavior import BehaviorLogBody, BehaviorLogResponse
from app.services import behavior_service

router = APIRouter(prefix="/behavior", tags=["behavior"])


@router.post(
    "/log",
    response_model=BehaviorLogResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit(max_requests=10, window_seconds=60))],
)
async def log_events(
    body: BehaviorLogBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    events = [ev.model_dump() for ev in body.events]
    accepted = await behavior_service.log_events(
        user=user, events=events, db=db
    )
    return {"accepted": accepted}


__all__ = ["router"]
