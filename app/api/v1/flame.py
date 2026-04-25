from __future__ import annotations

"""
Flame routes — QR token rotatif pour insta-match IRL.

Endpoints :
- GET  /flame/me            : récupère ou crée le QR token (rotate auto si >24h).
- PATCH /flame/me           : modifie scan_enabled / scans_received_max.
- GET  /flame/received-scans : historique scans reçus (sécurité).
- POST /matches/instant déclaré dans matches.py (cohérence du domain match).
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import get_current_user, get_db
from app.core.exceptions import AppException
from app.models.event import Event
from app.models.event_checkin import EventCheckin
from app.models.flame_scan_attempt import FlameScanAttempt
from app.models.user import User
from app.models.user_spot import UserSpot
from app.services import flame_service


router = APIRouter(prefix="/flame", tags=["flame"])
settings = get_settings()


# ── Schemas ─────────────────────────────────────────────────────────


class FlameMeResponse(BaseModel):
    qr_token: str
    expires_at: str  # ISO-8601 (rotated_at + 24h)
    scan_enabled: bool
    scans_received_max: int
    scans_received_max_cap: int  # plafond env (limite haute du slider mobile)
    scans_sent_per_day: int  # limite envois (lecture seule, env)


class FlameUpdateBody(BaseModel):
    scan_enabled: bool | None = None
    scans_received_max: int | None = Field(default=None, ge=1)
    # Location éphémère pour proximity check au scan. Le mobile envoie sa
    # position quand l'user ouvre l'écran flame ou lance un check-in
    # explicite. Stockée pour <flame_scan_checkin_window_min minutes.
    last_lat: float | None = Field(default=None, ge=-90, le=90)
    last_lng: float | None = Field(default=None, ge=-180, le=180)


# ── Routes ──────────────────────────────────────────────────────────


@router.get("/me", response_model=FlameMeResponse)
async def get_my_flame(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Récupère le QR token actuel. Si absent ou expiré (>24h), un nouveau
    est généré automatiquement. Idempotent.
    """
    flame = await flame_service.get_or_create_flame(user, db)
    expires_at = flame.rotated_at + timedelta(
        hours=flame_service.TOKEN_VALIDITY_HOURS,
    )
    return {
        "qr_token": flame.qr_token,
        "expires_at": expires_at.isoformat(),
        "scan_enabled": user.flame_scan_enabled,
        "scans_received_max": user.flame_scans_received_max,
        "scans_received_max_cap": settings.flame_scans_received_per_day,
        "scans_sent_per_day": settings.flame_scans_sent_per_day,
    }


@router.patch("/me", response_model=FlameMeResponse)
async def update_my_flame(
    body: FlameUpdateBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Modifie le toggle `scan_enabled` ou le plafond `scans_received_max`.
    Le plafond max est borné par `FLAME_SCANS_RECEIVED_PER_DAY` env :
    l'user peut baisser, jamais dépasser.
    """
    if body.scan_enabled is not None:
        user.flame_scan_enabled = body.scan_enabled

    if body.scans_received_max is not None:
        cap = settings.flame_scans_received_per_day
        if body.scans_received_max > cap:
            raise AppException(
                400, f"scans_received_max_above_cap:{cap}",
            )
        user.flame_scans_received_max = body.scans_received_max

    # Location éphémère : si fournie, on persist (les 2 doivent être présents).
    if body.last_lat is not None and body.last_lng is not None:
        user.last_lat = body.last_lat
        user.last_lng = body.last_lng
        user.last_location_at = datetime.now(timezone.utc)

    await db.commit()
    return await get_my_flame(user, db)


@router.get("/received-scans")
async def get_received_scans(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Historique des tentatives de scan reçues (sécurité).

    Free : count agrégé du jour seulement (pas de détail).
    Premium : liste détaillée des 30 dernières tentatives avec status,
    timestamp, et flag pour détecter les patterns suspects.

    Permet aux femmes (surtout) de détecter QR fuités et harceleurs
    persistants sans casser la fluidité IRL.
    """
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if not user.is_premium:
        # Free : agrégat du jour
        result = await db.execute(
            select(func.count(FlameScanAttempt.id)).where(
                FlameScanAttempt.target_id == user.id,
                FlameScanAttempt.at >= today,
            ),
        )
        return {
            "is_premium_view": False,
            "today_count": int(result.scalar_one() or 0),
            "items": [],
        }

    # Premium : 30 derniers
    result = await db.execute(
        select(FlameScanAttempt)
        .where(FlameScanAttempt.target_id == user.id)
        .order_by(FlameScanAttempt.at.desc())
        .limit(30),
    )
    attempts = result.scalars().all()
    return {
        "is_premium_view": True,
        "today_count": sum(1 for a in attempts if a.at >= today),
        "items": [
            {
                "at": a.at.isoformat(),
                "status": a.status,
                "scanner_id": str(a.scanner_id),
                "event_id": str(a.event_id) if a.event_id else None,
            }
            for a in attempts
        ],
    }


@router.get("/nearby")
async def get_nearby_count(
    event_id: UUID | None = Query(default=None),
    spot_id: UUID | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Renvoie le count de users récemment checked-in à un event ou un spot.

    Ne LEAK aucun user_id (privacy) — juste le count + nom du venue.
    Utilisé par le mobile pour afficher "🔥 12 personnes ici" sur le
    FAB ou sur l'écran event/spot.

    Query params : event_id OU spot_id (au moins l'un des deux).
    """
    if event_id is None and spot_id is None:
        raise AppException(400, "event_id_or_spot_id_required")

    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=settings.flame_scan_checkin_window_min,
    )

    if event_id is not None:
        ev = await db.get(Event, event_id)
        if ev is None:
            raise AppException(404, "event_not_found")
        result = await db.execute(
            select(func.count(func.distinct(EventCheckin.user_id))).where(
                EventCheckin.event_id == event_id,
                EventCheckin.at >= cutoff,
            ),
        )
        count = result.scalar_one() or 0
        # On exclut soi-même du count
        result_self = await db.execute(
            select(func.count(EventCheckin.id)).where(
                EventCheckin.event_id == event_id,
                EventCheckin.user_id == user.id,
                EventCheckin.at >= cutoff,
            ),
        )
        if (result_self.scalar_one() or 0) > 0:
            count = max(0, count - 1)
        return {
            "count": int(count),
            "event_id": str(event_id),
            "event_name": ev.title,
        }

    # spot_id : on regarde UserSpot.last_checkin_at < cutoff
    result = await db.execute(
        select(func.count(UserSpot.id)).where(
            UserSpot.spot_id == spot_id,
            UserSpot.user_id != user.id,
            UserSpot.last_checkin_at >= cutoff,
        ),
    )
    count = result.scalar_one() or 0
    return {
        "count": int(count),
        "spot_id": str(spot_id),
    }


__all__ = ["router"]
