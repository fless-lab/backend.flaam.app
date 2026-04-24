from __future__ import annotations

"""Schemas Pydantic Spots (spec §5.5)."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


SpotCategory = Literal[
    "cafe",
    "restaurant",
    "gym",
    "coworking",
    "bar",
    "worship",
    "market",
    "beach",
    "park",
    # West Africa-specific categories (see core/constants.py for social weights)
    "maquis",
    "club",
    "cultural",
    "other",
]

FidelityLevel = Literal["declared", "confirmed", "regular", "regular_plus"]


class SpotOut(BaseModel):
    id: UUID
    name: str
    category: SpotCategory
    city_id: UUID
    latitude: float
    longitude: float
    address: str | None = None
    total_checkins: int
    total_users: int
    is_verified: bool


class SpotDetailResponse(SpotOut):
    fidelity_distribution: dict[str, int]
    # {"declared": 4, "confirmed": 2, "regular": 1, "regular_plus": 0}


class AddSpotBody(BaseModel):
    spot_id: UUID


class CheckinBody(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)


class CheckinResponse(BaseModel):
    spot_id: UUID
    spot_name: str
    checkin_count: int
    fidelity_level: FidelityLevel
    previous_level: FidelityLevel
    level_upgraded: bool


class SpotVisibilityBody(BaseModel):
    is_visible: bool


class SuggestSpotBody(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    category: SpotCategory
    city_id: UUID
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    address: str | None = Field(default=None, max_length=300)


__all__ = [
    "SpotCategory",
    "FidelityLevel",
    "SpotOut",
    "SpotDetailResponse",
    "AddSpotBody",
    "CheckinBody",
    "CheckinResponse",
    "SpotVisibilityBody",
    "SuggestSpotBody",
]
