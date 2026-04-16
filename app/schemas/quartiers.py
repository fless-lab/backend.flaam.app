from __future__ import annotations

"""Schemas Pydantic Quartiers (spec §5.4)."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


QuartierRelationType = Literal["lives", "works", "hangs", "interested"]


class QuartierOut(BaseModel):
    id: UUID
    name: str
    latitude: float
    longitude: float


class AddQuartierBody(BaseModel):
    quartier_id: UUID
    relation_type: QuartierRelationType
    is_primary: bool = False


class UserQuartierOut(BaseModel):
    id: UUID
    quartier: QuartierOut
    relation_type: QuartierRelationType
    is_primary: bool


class QuartierLimit(BaseModel):
    current: int
    max: int
    max_premium: int | None = None


class MyQuartiersResponse(BaseModel):
    lives: list[dict]
    works: list[dict]
    hangs: list[dict]
    interested: list[dict]
    limits: dict[str, QuartierLimit]


class NearbyQuartier(BaseModel):
    id: UUID
    name: str
    proximity: float = Field(..., ge=0.0, le=1.0)
    distance_km: float


class NearbyQuartiersResponse(BaseModel):
    quartier: QuartierOut
    nearby: list[NearbyQuartier]


__all__ = [
    "QuartierRelationType",
    "QuartierOut",
    "AddQuartierBody",
    "UserQuartierOut",
    "QuartierLimit",
    "MyQuartiersResponse",
    "NearbyQuartier",
    "NearbyQuartiersResponse",
]
