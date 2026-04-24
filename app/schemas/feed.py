from __future__ import annotations

"""Schemas Pydantic pour le module Feed (spec §5.6)."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.photos import PhotoResponse


# ── Sous-objets ──────────────────────────────────────────────────────

class FeedQuartier(BaseModel):
    quartier_id: UUID
    name: str
    relation_type: Literal["lives", "works", "hangs", "interested"]


class FeedSpotInCommon(BaseModel):
    spot_id: UUID
    name: str
    category: str
    their_fidelity: str
    your_fidelity: str


class FeedPromptEntry(BaseModel):
    question: str
    answer: str
    # Optionnel : ID stable du prompt (utilisé par like.liked_prompt)
    prompt_id: str | None = None


class FeedProfileItem(BaseModel):
    """Profil affiché dans le feed."""

    id: UUID
    user_id: UUID
    display_name: str
    age: int
    intention: str | None = None
    sector: str | None = None
    rhythm: str | None = None
    bio: str | None = None

    photos: list[PhotoResponse]
    prompts: list[FeedPromptEntry]
    tags: list[str]
    tags_in_common: list[str]
    languages: list[str]

    quartiers: list[FeedQuartier]
    spots_in_common: list[FeedSpotInCommon]

    geo_score_display: int = Field(..., ge=0, le=100)
    is_verified: bool
    is_new_user: bool
    is_wildcard: bool
    # Derniere activite du user (timezone UTC). Le mobile affiche un
    # badge "actif maintenant" si now - last_active_at <= 15 min.
    last_active_at: datetime | None = None


# ── Responses ────────────────────────────────────────────────────────

class DailyFeedResponse(BaseModel):
    feed_date: date
    profiles: list[FeedProfileItem]
    remaining_likes: int
    is_premium: bool
    next_refresh_at: datetime


class CrossedFeedResponse(BaseModel):
    """Section 'Déjà croisés' — profils vus mais pas encore actionnés."""

    profiles: list[FeedProfileItem]


# ── Actions bodies ───────────────────────────────────────────────────

class LikeBody(BaseModel):
    liked_prompt: str | None = Field(default=None, max_length=100)
    # ── Targeted like (Feature A, Session 9) ──
    # Honoré uniquement quand flag_targeted_likes_enabled = 1.0.
    # Sinon ces 3 champs sont ignorés silencieusement.
    target_type: Literal["profile", "photo", "prompt"] | None = None
    target_id: str | None = Field(default=None, max_length=100)
    comment: str | None = Field(default=None, max_length=200)


class LikeResponse(BaseModel):
    status: Literal["liked", "matched", "already_liked"]
    match_id: UUID | None = None
    ice_breaker: str | None = None
    remaining_likes: int


class SkipBody(BaseModel):
    reason: (
        Literal["not_my_type", "too_far", "different_intentions", "no_reason"]
        | None
    ) = None


class SkipResponse(BaseModel):
    status: Literal["skipped", "already_skipped"]
    will_reappear_after: date


class ViewBody(BaseModel):
    duration_seconds: float = Field(..., ge=0.0, le=600.0)
    scrolled_full: bool = False
    prompts_viewed: int = Field(default=0, ge=0, le=10)


__all__ = [
    "FeedQuartier",
    "FeedSpotInCommon",
    "FeedPromptEntry",
    "FeedProfileItem",
    "DailyFeedResponse",
    "CrossedFeedResponse",
    "LikeBody",
    "LikeResponse",
    "SkipBody",
    "SkipResponse",
    "ViewBody",
]
