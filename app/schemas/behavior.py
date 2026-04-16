from __future__ import annotations

"""Schemas Pydantic — Behavior logs (§5.13)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


EventType = Literal[
    "profile_viewed",
    "photo_scrolled",
    "prompt_read",
    "return_visit",
    "scroll_depth",
    "app_session_start",
    "app_session_end",
]


class BehaviorEventItem(BaseModel):
    event_type: EventType
    target_user_id: UUID | None = None
    data: dict | None = None
    timestamp: datetime | None = None


class BehaviorLogBody(BaseModel):
    events: list[BehaviorEventItem] = Field(..., min_length=1, max_length=100)


class BehaviorLogResponse(BaseModel):
    accepted: int


__all__ = [
    "EventType",
    "BehaviorEventItem",
    "BehaviorLogBody",
    "BehaviorLogResponse",
]
