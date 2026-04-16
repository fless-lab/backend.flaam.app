from __future__ import annotations

"""Schemas Pydantic pour les notifications (§5.10)."""

from pydantic import BaseModel, Field


class NotificationPreferencesBody(BaseModel):
    new_match: bool | None = None
    new_message: bool | None = None
    daily_feed: bool | None = None
    events: bool | None = None
    date_reminder: bool | None = None
    weekly_digest: bool | None = None
    reply_reminders: bool | None = None
    daily_feed_hour: int | None = Field(default=None, ge=0, le=23)
    quiet_start_hour: int | None = Field(default=None, ge=0, le=23)
    quiet_end_hour: int | None = Field(default=None, ge=0, le=23)


class NotificationPreferencesResponse(BaseModel):
    new_match: bool
    new_message: bool
    daily_feed: bool
    events: bool
    date_reminder: bool
    weekly_digest: bool
    reply_reminders: bool
    daily_feed_hour: int
    quiet_start_hour: int
    quiet_end_hour: int


class FcmTokenBody(BaseModel):
    fcm_token: str = Field(..., min_length=8, max_length=512)
    device_fingerprint: str = Field(..., min_length=4, max_length=256)
    platform: str | None = Field(default=None, max_length=10)


class FcmTokenResponse(BaseModel):
    status: str = "updated"


__all__ = [
    "NotificationPreferencesBody",
    "NotificationPreferencesResponse",
    "FcmTokenBody",
    "FcmTokenResponse",
]
