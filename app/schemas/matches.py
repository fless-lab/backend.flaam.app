from __future__ import annotations

"""Schemas Pydantic pour le module Matches (spec §5.7)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.schemas.feed import FeedProfileItem


# ── Sous-objets ──────────────────────────────────────────────────────

class MatchedUserSummary(BaseModel):
    user_id: UUID
    display_name: str
    age: int
    photo_url: str | None = None
    is_verified: bool


class LastMessagePreview(BaseModel):
    id: UUID
    sender_id: UUID
    content: str | None
    message_type: str
    created_at: datetime


# ── Responses ────────────────────────────────────────────────────────

class MatchSummary(BaseModel):
    """Ligne dans la liste /matches."""

    match_id: UUID
    user: MatchedUserSummary
    matched_at: datetime
    last_message: LastMessagePreview | None = None
    unread_count: int = 0
    ice_breaker: str | None = None


class MatchListResponse(BaseModel):
    matches: list[MatchSummary]


class MatchDetailResponse(BaseModel):
    match_id: UUID
    status: Literal["matched", "pending", "unmatched", "expired"]
    user: FeedProfileItem
    matched_at: datetime | None
    expires_at: datetime | None
    ice_breaker: str


class UnmatchResponse(BaseModel):
    match_id: UUID
    status: Literal["unmatched"]


class LikesReceivedResponse(BaseModel):
    """Feed-like : gens qui t'ont liké et avec qui tu n'as pas matché."""

    profiles: list[FeedProfileItem]


__all__ = [
    "MatchedUserSummary",
    "LastMessagePreview",
    "MatchSummary",
    "MatchListResponse",
    "MatchDetailResponse",
    "UnmatchResponse",
    "LikesReceivedResponse",
]
