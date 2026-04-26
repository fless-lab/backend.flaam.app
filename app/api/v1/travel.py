"""Routes Travel mode + home city change.

Travel mode :
  - GET    /profiles/me/travel          — état actuel
  - POST   /profiles/me/travel          — activer (city_id + duration)
  - POST   /profiles/me/travel/extend   — prolonger +7j (1× par session)
  - DELETE /profiles/me/travel          — désactiver

Home city :
  - PATCH  /profiles/me/city            — changer la ville principale
                                          (cooldown 30 jours)

Le mode voyage bascule temporairement le feed/discovery sur une autre
ville. La ville principale (city_id) ne change que via PATCH /city.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.core.i18n import detect_lang
from app.models.user import User
from app.schemas.travel import (
    CityChangeBody,
    CityChangeResponse,
    TravelActivateBody,
    TravelStatusResponse,
)
from app.services import travel_service


router = APIRouter(prefix="/profiles/me", tags=["travel"])


@router.get("/travel", response_model=TravelStatusResponse)
async def get_travel(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TravelStatusResponse:
    return await travel_service.get_status(user, db, detect_lang(request))


@router.post("/travel", response_model=TravelStatusResponse)
async def activate_travel(
    body: TravelActivateBody,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TravelStatusResponse:
    return await travel_service.activate(
        user, body.city_id, body.duration_days, db, detect_lang(request),
    )


@router.post("/travel/extend", response_model=TravelStatusResponse)
async def extend_travel(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TravelStatusResponse:
    return await travel_service.extend(user, db, detect_lang(request))


@router.delete("/travel", response_model=TravelStatusResponse)
async def deactivate_travel(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TravelStatusResponse:
    return await travel_service.deactivate(user, db, detect_lang(request))


@router.patch("/city", response_model=CityChangeResponse)
async def change_home_city(
    body: CityChangeBody,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CityChangeResponse:
    return await travel_service.change_home_city(
        user, body.city_id, db, detect_lang(request),
    )
