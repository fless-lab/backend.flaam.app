from __future__ import annotations

"""Schemas Pydantic pour les endpoints Events (§5.9 + MàJ 8 Porte 3)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


EventStatus = Literal[
    "draft", "published", "full", "ongoing", "completed", "cancelled"
]
EventCategory = Literal[
    "afterwork",
    "sport",
    "brunch",
    "cultural",
    "networking",
    "workshop",
    "outdoor",
]


class EventListItem(BaseModel):
    id: UUID
    title: str
    description: str | None = None
    category: str
    status: str
    starts_at: datetime
    ends_at: datetime | None = None
    spot_id: UUID
    spot_name: str | None = None
    city_id: UUID
    max_attendees: int | None = None
    current_attendees: int
    slug: str | None = None


class EventListResponse(BaseModel):
    events: list[EventListItem]


class EventDetailResponse(EventListItem):
    is_sponsored: bool = False
    sponsor_name: str | None = None
    is_registered: bool = False
    registration_status: str | None = None


class EventRegisterResponse(BaseModel):
    status: str  # "registered" | "already_registered"
    event_id: UUID
    current_attendees: int
    max_attendees: int | None


class EventUnregisterResponse(BaseModel):
    status: Literal["unregistered"] = "unregistered"
    event_id: UUID
    current_attendees: int


class MatchesPreviewProfile(BaseModel):
    user_id: UUID
    display_name: str
    primary_photo_url: str | None = None
    geo_score: int  # 0-100 arrondi
    lifestyle_score: int


class MatchesPreviewResponse(BaseModel):
    total_compatible: int
    top: list[MatchesPreviewProfile]


# ── Event preregister (Porte 3, public) ───────────────────────────────

class EventPreregisterBody(BaseModel):
    phone: str
    event_id: UUID


class EventPreregisterResponse(BaseModel):
    otp_sent: bool
    channel: str
    event_name: str
    expires_in: int


class EventPreregisterVerifyBody(BaseModel):
    phone: str
    code: str = Field(..., min_length=4, max_length=8)
    event_id: UUID
    first_name: str = Field(..., min_length=2, max_length=50)


class EventPreregisterVerifyResponse(BaseModel):
    status: Literal["registered", "existing_user"]
    qr_code: str | None = None
    qr_code_url: str | None = None
    event_name: str
    event_date: datetime
    message: str


# ── Checkin ───────────────────────────────────────────────────────────

class EventCheckinBody(BaseModel):
    qr_code: str = Field(..., min_length=8, max_length=256)


class EventCheckinResponse(BaseModel):
    status: Literal["checked_in"]
    event_id: UUID
    user_id: UUID
    attendees_count: int


# ── Self check-in GPS (mobile, l'user lui-même valide qu'il est sur place) ──

class EventSelfCheckinBody(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


class EventSelfCheckinResponse(BaseModel):
    status: Literal["checked_in"]
    event_id: UUID
    user_id: UUID
    distance_to_venue_m: int


# ── Stats anonymes (public) ───────────────────────────────────────────

class EventStatsResponse(BaseModel):
    event_id: UUID
    event_name: str
    event_date: datetime
    registered_count: int
    checked_in_count: int
    spots_left: int | None = None
    quartier_breakdown: dict[str, int] = Field(default_factory=dict)


__all__ = [
    "EventStatus",
    "EventCategory",
    "EventListItem",
    "EventListResponse",
    "EventDetailResponse",
    "EventRegisterResponse",
    "EventUnregisterResponse",
    "MatchesPreviewProfile",
    "MatchesPreviewResponse",
    "EventPreregisterBody",
    "EventPreregisterResponse",
    "EventPreregisterVerifyBody",
    "EventPreregisterVerifyResponse",
    "EventCheckinBody",
    "EventCheckinResponse",
    "EventSelfCheckinBody",
    "EventSelfCheckinResponse",
    "EventStatsResponse",
]
