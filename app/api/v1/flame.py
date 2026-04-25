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

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import get_current_user, get_db
from app.core.exceptions import AppException
from app.models.user import User
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


__all__ = ["router"]
