from __future__ import annotations

"""Routes Events (§5.9 + MàJ 8 Porte 3)."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.core.i18n import detect_lang
from app.models.user import User
from app.schemas.events import (
    EventCheckinBody,
    EventCheckinResponse,
    EventDetailResponse,
    EventListResponse,
    EventRegisterResponse,
    EventSelfCheckinBody,
    EventSelfCheckinResponse,
    EventStatsResponse,
    EventUnregisterResponse,
    MatchesPreviewResponse,
)
from app.services import event_service

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=EventListResponse)
async def list_events(
    city_id: UUID | None = Query(default=None),
    from_date: datetime | None = Query(default=None, alias="from"),
    to_date: datetime | None = Query(default=None, alias="to"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    events = await event_service.list_events(
        city_id=city_id,
        from_date=from_date,
        to_date=to_date,
        user_id=user.id,
        db=db,
    )
    return {"events": events}


@router.get("/{event_id}/stats", response_model=EventStatsResponse)
async def event_stats(
    event_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Public : stats anonymes pour la page web event (page Porte 3)."""
    return await event_service.get_event_stats(event_id, db)


@router.get(
    "/{event_id}/matches-preview", response_model=MatchesPreviewResponse
)
async def event_matches_preview(
    event_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await event_service.matches_preview(
        event_id, user, db, lang=detect_lang(request)
    )


@router.get("/{event_id}", response_model=EventDetailResponse)
async def event_detail(
    event_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await event_service.get_event_detail(
        event_id, user, db, lang=detect_lang(request)
    )


@router.post("/{event_id}/register", response_model=EventRegisterResponse)
async def register_event(
    event_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await event_service.register_to_event(
        event_id, user, db, lang=detect_lang(request)
    )


@router.delete(
    "/{event_id}/register", response_model=EventUnregisterResponse
)
async def unregister_event(
    event_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await event_service.unregister_from_event(
        event_id, user, db, lang=detect_lang(request)
    )


@router.post(
    "/{event_id}/checkin",
    response_model=EventCheckinResponse,
    status_code=status.HTTP_200_OK,
)
async def checkin_event(
    event_id: UUID,
    body: EventCheckinBody,
    request: Request,
    _staff: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Check-in QR à l'entrée de l'event.
    Au MVP : n'importe quel user authentifié peut scanner (c'est le
    device staff qui utilise son propre compte Flaam). Le rôle staff
    dédié viendra en Session 10.
    """
    return await event_service.checkin_event(
        event_id, body.qr_code, db, lang=detect_lang(request)
    )


@router.post(
    "/{event_id}/self-checkin",
    response_model=EventSelfCheckinResponse,
)
async def self_checkin_event(
    event_id: UUID,
    body: EventSelfCheckinBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Self check-in GPS — l'user valide qu'il est physiquement présent à
    l'event en envoyant ses coordonnées. Le backend vérifie qu'il est
    < 200m du venue et dans la fenêtre temporelle (event start-1h →
    event end+2h). Crée un EventCheckin et alimente flame/nearby.
    """
    return await event_service.self_checkin_event(
        event_id, user, body.lat, body.lng, db,
    )


@router.get("/{event_id}/present")
async def get_event_present(
    event_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Liste les users actuellement checked-in à cet event (<2h)."""
    return await event_service.list_present(event_id, user, db)


__all__ = ["router"]
