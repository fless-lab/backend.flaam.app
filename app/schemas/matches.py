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


class LikesReceivedPreview(BaseModel):
    """Aperçu flouté pour user free (identification minimale)."""

    blurred_photo_url: str | None = None
    first_letter: str


class LikesReceivedResponse(BaseModel):
    """
    Réponse 2-tier (voir docs/flaam-business-model.md).

    - Free   : total_count + 3 aperçus floutés + message i18n (FR/EN selon
      Accept-Language).
    - Premium: total_count + profils complets.
    """

    is_premium_user: bool
    total_count: int
    # Mode free
    preview: list[LikesReceivedPreview] | None = None
    message: str | None = None
    # Mode premium
    profiles: list[FeedProfileItem] | None = None


__all__ = [
    "MatchedUserSummary",
    "LastMessagePreview",
    "MatchSummary",
    "MatchListResponse",
    "MatchDetailResponse",
    "UnmatchResponse",
    "LikesReceivedPreview",
    "LikesReceivedResponse",
]
