from __future__ import annotations

"""Schemas Pydantic — Safety (§5.11)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


ReportReason = Literal[
    "inappropriate_content",
    "fake_profile",
    "harassment",
    "scam",
    "underage",
    "other",
]


class ReportBody(BaseModel):
    reported_user_id: UUID
    reason: ReportReason
    description: str | None = Field(default=None, max_length=1000)
    evidence_message_ids: list[UUID] | None = None


class ReportResponse(BaseModel):
    id: UUID
    status: str
    message: str | None = None


class BlockBody(BaseModel):
    blocked_user_id: UUID


class BlockResponse(BaseModel):
    status: Literal["blocked"]
    blocked_user_id: UUID
    message: str | None = None


class UnblockResponse(BaseModel):
    status: Literal["unblocked"]
    message: str | None = None


class ShareDateBody(BaseModel):
    contact_phone: str = Field(..., min_length=8, max_length=20)
    contact_name: str | None = Field(default=None, max_length=100)
    partner_name: str = Field(..., min_length=1, max_length=100)
    meeting_place: str = Field(..., min_length=1, max_length=200)
    meeting_time: datetime


class ShareDateResponse(BaseModel):
    status: Literal["sent"]
    provider_message_id: str | None = None


class EmergencyBody(BaseModel):
    contact_phone: str = Field(..., min_length=8, max_length=20)
    contact_name: str | None = Field(default=None, max_length=100)
    timer_hours: int = Field(default=3, ge=1, le=24)
    # Position optionnelle (GPS) — transmise au contact si le timer expire.
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    meeting_place: str | None = Field(default=None, max_length=200)


class EmergencyResponse(BaseModel):
    status: Literal["armed"]
    expires_at: datetime
    message: str | None = None


class TimerCancelResponse(BaseModel):
    status: Literal["cancelled", "no_active_timer"]
    message: str | None = None


__all__ = [
    "ReportReason",
    "ReportBody",
    "ReportResponse",
    "BlockBody",
    "BlockResponse",
    "UnblockResponse",
    "ShareDateBody",
    "ShareDateResponse",
    "EmergencyBody",
    "EmergencyResponse",
    "TimerCancelResponse",
]
