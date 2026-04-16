from __future__ import annotations

"""Schemas Pydantic Cities / Countries / Waitlist (spec MàJ villes/pays, §5)."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


CityPhase = Literal["hidden", "teaser", "launch", "growth", "stable"]


class CityWaitlistInfo(BaseModel):
    total_registered: int
    threshold: int
    remaining: int


class CityOut(BaseModel):
    id: UUID
    name: str
    country_code: str
    country_name: str
    country_flag: str | None = None
    phase: CityPhase
    selectable: bool
    waitlist: CityWaitlistInfo | None = None


class CitiesByCountryResponse(BaseModel):
    country_code: str
    country_name: str
    country_flag: str | None = None
    cities: list[CityOut]


class CountryOut(BaseModel):
    country_code: str
    country_name: str
    country_flag: str | None = None
    phone_prefix: str | None = None
    active_cities_count: int
    teaser_cities_count: int


class CountriesResponse(BaseModel):
    countries: list[CountryOut]


class LaunchStatusResponse(BaseModel):
    city_id: UUID
    phase: CityPhase
    total_registered: int
    male_registered: int
    female_registered: int
    waitlist_threshold: int
    remaining_to_launch: int


WaitlistStatus = Literal["activated", "waiting", "invited", "expired"]


class JoinWaitlistResponse(BaseModel):
    status: WaitlistStatus
    position: int = Field(default=0, ge=0)
    total_waiting: int | None = None
    message: str


__all__ = [
    "CityPhase",
    "CityWaitlistInfo",
    "CityOut",
    "CitiesByCountryResponse",
    "CountryOut",
    "CountriesResponse",
    "LaunchStatusResponse",
    "WaitlistStatus",
    "JoinWaitlistResponse",
]
