from __future__ import annotations

"""Routes Safety (§5.11, S12.5). 13 endpoints."""

from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db, get_redis
from app.core.i18n import detect_lang, t
from app.core.rate_limiter import rate_limit
from app.models.user import User
from app.schemas.safety import (
    BlockBody,
    BlockResponse,
    EmergencyBody,
    EmergencyContactBody,
    EmergencyContactResponse,
    EmergencyContactUpdate,
    EmergencyResponse,
    PanicBody,
    PanicResponse,
    ReportBody,
    ReportResponse,
    ShareDateBody,
    ShareDateResponse,
    TimerCancelResponse,
    TimerExtendBody,
    TimerExtendResponse,
    TimerLocationBody,
    TimerLocationResponse,
    UnblockResponse,
)
from app.services import safety_service

router = APIRouter(prefix="/safety", tags=["safety"])


# ══════════════════════════════════════════════════════════════════════
# Report / Block / Share-date
# ══════════════════════════════════════════════════════════════════════


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


# ══════════════════════════════════════════════════════════════════════
# Emergency contacts CRUD (S12.5)
# ══════════════════════════════════════════════════════════════════════


@router.get(
    "/contacts",
    response_model=list[EmergencyContactResponse],
)
async def list_contacts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    contacts = await safety_service.list_emergency_contacts(
        user=user, db=db
    )
    return [
        {
            "id": c.id,
            "name": c.name,
            "phone": c.phone,
            "is_primary": c.is_primary,
        }
        for c in contacts
    ]


@router.post(
    "/contacts",
    response_model=EmergencyContactResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_contact(
    body: EmergencyContactBody,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    lang = detect_lang(request)
    contact = await safety_service.create_emergency_contact(
        user=user, name=body.name, phone=body.phone, db=db, lang=lang
    )
    return {
        "id": contact.id,
        "name": contact.name,
        "phone": contact.phone,
        "is_primary": contact.is_primary,
    }


@router.put(
    "/contacts/{contact_id}",
    response_model=EmergencyContactResponse,
)
async def update_contact(
    contact_id: UUID,
    body: EmergencyContactUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    lang = detect_lang(request)
    contact = await safety_service.update_emergency_contact(
        user=user,
        contact_id=contact_id,
        name=body.name,
        phone=body.phone,
        db=db,
        lang=lang,
    )
    return {
        "id": contact.id,
        "name": contact.name,
        "phone": contact.phone,
        "is_primary": contact.is_primary,
    }


@router.delete(
    "/contacts/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_contact(
    contact_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    lang = detect_lang(request)
    await safety_service.delete_emergency_contact(
        user=user, contact_id=contact_id, db=db, lang=lang
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/contacts/{contact_id}/primary",
    response_model=EmergencyContactResponse,
)
async def patch_contact_primary(
    contact_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    lang = detect_lang(request)
    contact = await safety_service.set_primary_contact(
        user=user, contact_id=contact_id, db=db, lang=lang
    )
    return {
        "id": contact.id,
        "name": contact.name,
        "phone": contact.phone,
        "is_primary": contact.is_primary,
    }


# ══════════════════════════════════════════════════════════════════════
# Emergency timer
# ══════════════════════════════════════════════════════════════════════


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
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    lang = detect_lang(request)
    expires_at, session_id = await safety_service.start_emergency_timer(
        user=user,
        hours=body.hours,
        contact_ids=body.contact_ids,
        contact_phone=body.contact_phone,
        contact_name=body.contact_name,
        latitude=body.latitude,
        longitude=body.longitude,
        meeting_place=body.meeting_place,
        match_id=body.match_id,
        partner_user_id=body.partner_user_id,
        db=db,
        redis=redis,
        lang=lang,
    )
    return {
        "status": "armed",
        "expires_at": expires_at,
        "session_id": session_id,
        "message": t("emergency_timer_started", lang, hours=body.hours),
    }


@router.post("/timer/cancel", response_model=TimerCancelResponse)
async def post_timer_cancel(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    lang = detect_lang(request)
    cancelled = await safety_service.cancel_emergency_timer(
        user=user, db=db, redis=redis
    )
    return {
        "status": "cancelled" if cancelled else "no_active_timer",
        "message": t("emergency_timer_cancelled", lang) if cancelled else None,
    }


@router.patch(
    "/timer/location",
    response_model=TimerLocationResponse,
)
async def patch_timer_location(
    body: TimerLocationBody,
    request: Request,
    user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    lang = detect_lang(request)
    await safety_service.update_timer_location(
        user=user,
        latitude=body.latitude,
        longitude=body.longitude,
        redis=redis,
        lang=lang,
    )
    return {"status": "updated"}


@router.post(
    "/timer/extend",
    response_model=TimerExtendResponse,
)
async def post_timer_extend(
    body: TimerExtendBody,
    request: Request,
    user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    lang = detect_lang(request)
    new_exp = await safety_service.extend_timer(
        user=user, extra_hours=body.extra_hours, redis=redis, lang=lang
    )
    return {
        "status": "extended",
        "expires_at": new_exp,
        "message": t("timer_extended", lang, hours=body.extra_hours),
    }


@router.post(
    "/emergency/trigger",
    response_model=PanicResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit(max_requests=5, window_seconds=3600))],
)
async def post_emergency_trigger(
    body: PanicBody,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    lang = detect_lang(request)
    notified = await safety_service.trigger_panic(
        user=user,
        latitude=body.latitude,
        longitude=body.longitude,
        db=db,
        redis=redis,
        lang=lang,
    )
    return {
        "status": "alert_sent",
        "contacts_notified": notified,
        "message": t("emergency_triggered", lang),
    }


__all__ = ["router"]
