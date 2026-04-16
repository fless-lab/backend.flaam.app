from __future__ import annotations

"""Routes Cities / Countries / Waitlist (MàJ villes/pays, §5)."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.models.user import User
from app.schemas.cities import (
    CitiesByCountryResponse,
    CountriesResponse,
    JoinWaitlistResponse,
    LaunchStatusResponse,
)
from app.services import city_service

router = APIRouter(tags=["cities"])


@router.get("/countries", response_model=CountriesResponse)
async def list_countries(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Public : liste des pays avec au moins 1 ville non-hidden."""
    return await city_service.get_available_countries(db)


@router.get("/cities", response_model=CitiesByCountryResponse)
async def list_cities(
    country_code: str = Query(..., min_length=2, max_length=2),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Public : villes d'un pays (hors hidden)."""
    return await city_service.get_cities_by_country(country_code, db)


@router.get("/cities/{city_id}/launch-status", response_model=LaunchStatusResponse)
async def city_launch_status(
    city_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await city_service.get_launch_status(city_id, db)


@router.post("/cities/{city_id}/waitlist/join", response_model=JoinWaitlistResponse)
async def join_waitlist(
    city_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await city_service.join_waitlist(user, city_id, db)


__all__ = ["router"]
