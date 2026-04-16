from __future__ import annotations

"""Routes Notifications (§5.10)."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.models.user import User
from app.schemas.notifications import (
    FcmTokenBody,
    FcmTokenResponse,
    NotificationPreferencesBody,
    NotificationPreferencesResponse,
)
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _prefs_to_dict(prefs) -> dict:
    return {
        "new_match": prefs.new_match,
        "new_message": prefs.new_message,
        "daily_feed": prefs.daily_feed,
        "events": prefs.events,
        "date_reminder": prefs.date_reminder,
        "weekly_digest": prefs.weekly_digest,
        "daily_feed_hour": prefs.daily_feed_hour,
        "quiet_start_hour": prefs.quiet_start_hour,
        "quiet_end_hour": prefs.quiet_end_hour,
    }


@router.get("/preferences", response_model=NotificationPreferencesResponse)
async def get_preferences(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    prefs = await notification_service.get_or_create_preferences(user, db)
    return _prefs_to_dict(prefs)


@router.put("/preferences", response_model=NotificationPreferencesResponse)
async def update_preferences(
    body: NotificationPreferencesBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    prefs = await notification_service.update_preferences(
        user, body.model_dump(exclude_none=True), db
    )
    return _prefs_to_dict(prefs)


@router.post("/fcm-token", response_model=FcmTokenResponse)
async def register_fcm_token(
    body: FcmTokenBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await notification_service.register_fcm_token(
        user,
        fcm_token=body.fcm_token,
        device_fingerprint=body.device_fingerprint,
        platform=body.platform,
        db=db,
    )
    return {"status": "updated"}


__all__ = ["router"]
