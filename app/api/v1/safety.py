from __future__ import annotations

"""Routes Safety (§5.11). 6 endpoints."""

from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request, status

from app.core.dependencies import get_current_user, get_db, get_redis
from app.core.i18n import detect_lang, t
from app.core.rate_limiter import rate_limit
from app.models.user import User
from app.schemas.safety import (
    BlockBody,
    BlockResponse,
    EmergencyBody,
    EmergencyResponse,
    ReportBody,
    ReportResponse,
    ShareDateBody,
    ShareDateResponse,
    TimerCancelResponse,
    UnblockResponse,
)
from app.services import safety_service
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/safety", tags=["safety"])


@router.post(
    "/report",
    response_model=ReportResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit(max_requests=5, window_seconds=3600))],
)
async def post_report(
    body: ReportBody,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    lang = detect_lang(request)
    report = await safety_service.report_user(
        reporter=user,
        reported_user_id=body.reported_user_id,
        reason=body.reason,
        description=body.description,
        evidence_message_ids=body.evidence_message_ids,
        db=db,
        lang=lang,
    )
    return {
        "id": report.id,
        "status": report.status,
        "message": t("report_submitted", lang),
    }


@router.post(
    "/block",
    response_model=BlockResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_block(
    body: BlockBody,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    lang = detect_lang(request)
    await safety_service.block_user(
        blocker=user,
        blocked_user_id=body.blocked_user_id,
        db=db,
        lang=lang,
    )
    return {
        "status": "blocked",
        "blocked_user_id": body.blocked_user_id,
        "message": t("user_blocked", lang),
    }


@router.delete("/block/{user_id}", response_model=UnblockResponse)
async def delete_block(
    user_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    lang = detect_lang(request)
    await safety_service.unblock_user(
        blocker=user, blocked_user_id=user_id, db=db
    )
    return {"status": "unblocked", "message": t("user_unblocked", lang)}


@router.post(
    "/share-date",
    response_model=ShareDateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_share_date(
    body: ShareDateBody,
    user: User = Depends(get_current_user),
) -> dict:
    result = await safety_service.share_date(
        user=user,
        contact_phone=body.contact_phone,
        contact_name=body.contact_name,
        partner_name=body.partner_name,
        meeting_place=body.meeting_place,
        meeting_time=body.meeting_time,
    )
    return {
        "status": "sent",
        "provider_message_id": result.get("message_id"),
    }


@router.post(
    "/emergency",
    response_model=EmergencyResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit(max_requests=5, window_seconds=3600))],
)
async def post_emergency(
    body: EmergencyBody,
    request: Request,
    user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    lang = detect_lang(request)
    expires_at = await safety_service.start_emergency_timer(
        user=user,
        contact_phone=body.contact_phone,
        contact_name=body.contact_name,
        timer_hours=body.timer_hours,
        latitude=body.latitude,
        longitude=body.longitude,
        meeting_place=body.meeting_place,
        redis=redis,
    )
    return {
        "status": "armed",
        "expires_at": expires_at,
        "message": t("emergency_timer_started", lang, hours=body.timer_hours),
    }


@router.post("/timer/cancel", response_model=TimerCancelResponse)
async def post_timer_cancel(
    request: Request,
    user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    lang = detect_lang(request)
    cancelled = await safety_service.cancel_emergency_timer(
        user=user, redis=redis
    )
    return {
        "status": "cancelled" if cancelled else "no_active_timer",
        "message": t("emergency_timer_cancelled", lang) if cancelled else None,
    }


__all__ = ["router"]
