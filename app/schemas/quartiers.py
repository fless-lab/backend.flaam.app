from __future__ import annotations

"""Schemas Pydantic Quartiers (spec §5.4)."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


# `hangs` retiré (doublon avec UserSpot, granularité plus fine).
QuartierRelationType = Literal["lives", "works", "interested"]


class QuartierOut(BaseModel):
    id: UUID
    name: str
    latitude: float
    longitude: float
    # Polygone GeoJSON sérialisé (#217 R&D Phase 3). Null si le quartier
    # n'a pas encore de zone définie (legacy point-only). Le mobile parse
    # cette string pour render le polygone sur osmdroid.
    area_geojson: str | None = None


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
