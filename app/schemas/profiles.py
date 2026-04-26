from __future__ import annotations

"""Schemas Pydantic pour le module Profiles (spec §5.2, §13)."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.photos import PhotoResponse


# ── Types fermés (spec §3.6) ─────────────────────────────────────────

Gender = Literal["man", "woman", "non_binary"]
SeekingGender = Literal["men", "women", "everyone"]
Intention = Literal["serious", "getting_to_know", "friendship_first", "open"]
Sector = Literal[
    "tech",
    "finance",
    "health",
    "education",
    "commerce",
    "creative",
    "public_admin",
    "student",
    "other",
]
Rhythm = Literal["early_bird", "night_owl", "flexible"]


class PromptEntry(BaseModel):
    question: str = Field(..., min_length=1, max_length=100)
    answer: str = Field(..., min_length=1, max_length=200)


# ── Requests ─────────────────────────────────────────────────────────

class UpdateProfileBody(BaseModel):
    """
    Tous les champs sont optionnels : le client peut envoyer seulement
    ce qu'il modifie. Les `prompts` / `tags` / `languages` remplacent
    les listes existantes quand ils sont fournis (pas de merge partiel).
    """

    display_name: str | None = Field(default=None, min_length=2, max_length=50)
    birth_date: date | None = None
    gender: Gender | None = None
    seeking_gender: SeekingGender | None = None
    intention: Intention | None = None
    sector: Sector | None = None
    rhythm: Rhythm | None = None

    bio: str | None = Field(default=None, max_length=500)

    # `prompts` reste accepté pour la rétro-compat des clients déjà
    # déployés. Le nouveau front ne l'envoie plus.
    prompts: list[PromptEntry] | None = Field(default=None, max_length=3)
    tags: list[str] | None = Field(default=None, max_length=8)
    languages: list[str] | None = Field(default=None, max_length=10)

    seeking_age_min: int | None = Field(default=None, ge=18, le=99)
    seeking_age_max: int | None = Field(default=None, ge=18, le=99)

    city_id: UUID | None = None

    # Mode de recherche géo (cf. User.feed_search_mode).
    feed_search_mode: Literal["whole_city", "specific_quartiers"] | None = None

    @field_validator("birth_date")
    @classmethod
    def _check_age(cls, v: date | None) -> date | None:
        if v is None:
            return v
        today = date.today()
        age = today.year - v.year - ((today.month, today.day) < (v.month, v.day))
        if age < 18:
            raise ValueError("must be 18+")
        if age > 99:
            raise ValueError("invalid birth_date")
        return v


# ── Responses ────────────────────────────────────────────────────────

class MyProfileResponse(BaseModel):
    id: UUID
    user_id: UUID
    display_name: str
    age: int
    birth_date: date
    gender: Gender
    seeking_gender: SeekingGender
    intention: Intention | None = None
    sector: Sector | None = None
    rhythm: Rhythm | None = None
    bio: str | None = None
    prompts: list[PromptEntry]
    tags: list[str]
    languages: list[str]
    seeking_age_min: int
    seeking_age_max: int
    photos: list[PhotoResponse]
    profile_completeness: float
    is_selfie_verified: bool
    is_id_verified: bool
    is_visible: bool
    city_id: UUID | None
    onboarding_step: str
    updated_at: datetime


class OtherProfileResponse(BaseModel):
    """Vue publique (sans prefs cherchées, sans contacts)."""

    id: UUID
    user_id: UUID
    display_name: str
    age: int
    gender: Gender
    intention: Intention | None = None
    sector: Sector | None = None
    rhythm: Rhythm | None = None
    bio: str | None = None
    prompts: list[PromptEntry]
    tags: list[str]
    languages: list[str]
    photos: list[PhotoResponse]
    is_selfie_verified: bool
    # Badge "En visite" : true si l'user vu est en mode voyage actif.
    # Permet au mobile d'afficher "À Lomé jusqu'au 5 mai" pour transparence.
    is_traveling: bool = False
    travel_city_name: str | None = None
    travel_until: datetime | None = None
    # `travel_confirmed` : preuve passive de présence dans la ville de
    # destination (GPS validé < 24h via check-in spot ou scan flame).
    # UI affiche "· Confirmé" en accent succès quand true.
    travel_confirmed: bool = False


class CompletenessBreakdown(BaseModel):
    step: str
    weight: float
    achieved: bool


class CompletenessResponse(BaseModel):
    score: float = Field(..., ge=0.0, le=1.0)
    breakdown: list[CompletenessBreakdown]


class VisibilityBody(BaseModel):
    is_visible: bool


class VisibilityResponse(BaseModel):
    is_visible: bool


# ── Onboarding (spec §13) ────────────────────────────────────────────

OnboardingStatus = Literal["completed", "in_progress", "pending"]


class OnboardingStepState(BaseModel):
    step: str
    status: OnboardingStatus
    skippable: bool = False
    detail: dict | None = None


class OnboardingResponse(BaseModel):
    current_step: str
    steps: list[OnboardingStepState]
    progress_percent: int
    profile_completeness: float


class OnboardingSkipBody(BaseModel):
    step: str


class OnboardingSkipResponse(BaseModel):
    skipped: str
    next_step: str
    warning: str | None = None


__all__ = [
    "Gender",
    "SeekingGender",
    "Intention",
    "Sector",
    "Rhythm",
    "PromptEntry",
    "UpdateProfileBody",
    "MyProfileResponse",
    "OtherProfileResponse",
    "CompletenessBreakdown",
    "CompletenessResponse",
    "VisibilityBody",
    "VisibilityResponse",
    "OnboardingStepState",
    "OnboardingResponse",
    "OnboardingSkipBody",
    "OnboardingSkipResponse",
]
