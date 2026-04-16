from __future__ import annotations

"""Routes Quartiers (§5.4)."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.models.user import User
from app.schemas.quartiers import (
    AddQuartierBody,
    MyQuartiersResponse,
    NearbyQuartiersResponse,
    QuartierOut,
    QuartierRelationType,
    UserQuartierOut,
)
from app.services import quartier_service

router = APIRouter(prefix="/quartiers", tags=["quartiers"])


@router.get("", response_model=list[QuartierOut])
async def list_quartiers(
    city_id: UUID = Query(...),
    _me: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    quartiers = await quartier_service.list_quartiers_by_city(city_id, db)
    return [
        {
            "id": q.id,
            "name": q.name,
            "latitude": q.latitude,
            "longitude": q.longitude,
        }
        for q in quartiers
    ]


@router.post("/me", response_model=UserQuartierOut, status_code=201)
async def add_my_quartier(
    body: AddQuartierBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    uq = await quartier_service.add_quartier_to_profile(
        user=user,
        quartier_id=body.quartier_id,
        relation_type=body.relation_type,
        is_primary=body.is_primary,
        db=db,
    )
    q = uq.quartier
    return {
        "id": uq.id,
        "quartier": {
            "id": q.id,
            "name": q.name,
            "latitude": q.latitude,
            "longitude": q.longitude,
        },
        "relation_type": uq.relation_type,
        "is_primary": uq.is_primary,
    }


@router.delete("/me/{quartier_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_my_quartier(
    quartier_id: UUID,
    relation_type: QuartierRelationType = Query(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await quartier_service.remove_quartier(user, quartier_id, relation_type, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=MyQuartiersResponse)
async def my_quartiers(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await quartier_service.get_my_quartiers(user, db)


@router.get("/{quartier_id}/nearby", response_model=NearbyQuartiersResponse)
async def nearby_quartiers(
    quartier_id: UUID,
    _me: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await quartier_service.get_nearby_quartiers(quartier_id, db)


__all__ = ["router"]
