from __future__ import annotations

"""Routes Matches (§5.7)."""

from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db, get_redis
from app.core.i18n import detect_lang
from app.models.user import User
from app.schemas.matches import (
    LikesReceivedResponse,
    MatchDetailResponse,
    MatchListResponse,
    SeenIrlResponse,
    UnmatchResponse,
)
from app.services import (
    feed_service,
    instant_match_service,
    match_service,
    seen_irl_service,
)


# ── Insta-match QR body ─────────────────────────────────────────────


class InstantMatchBody(BaseModel):
    """Body de POST /matches/instant. lat/lng optionnels si event_id fourni."""
    scanned_qr_token: str = Field(..., min_length=20, max_length=64)
    scanner_lat: float | None = Field(default=None, ge=-90, le=90)
    scanner_lng: float | None = Field(default=None, ge=-180, le=180)
    event_id: UUID | None = None


class InstantMatchResponse(BaseModel):
    match_id: UUID
    other_user_id: UUID
    other_display_name: str
    icebreaker: str
    is_idempotent: bool  # True si match existait déjà <24h
    # Signal de sécurité (pas bloqueur) : selfie vérifié côté target ?
    # Le mobile affiche un badge ⚠️ "Compte non vérifié" si false.
    # Pas anxiogène, pas pop-up — juste un signal couleur.
    target_verified: bool

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("", response_model=MatchListResponse)
async def list_my_matches(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await match_service.list_matches(user, db)


# /instant doit être déclaré AVANT /{match_id} pour éviter le route conflict.
@router.post("/instant", response_model=InstantMatchResponse)
async def create_instant_match_endpoint(
    body: InstantMatchBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Crée un match direct via scan QR IRL. Bypass le double-like.

    Vérifications (cf. instant_match_service) :
    - Token valide non expiré
    - target.flame_scan_enabled = true
    - Hard filters bidir (city, gender, age, blocked, selfie)
    - Idempotence 24h
    - Rate limits scans envoyés (5/jour) + reçus (max user-controlled)
    - Proximity GPS <100m OU event_id partagé récent
    """
    # Pré-check le matched user existe / hors-bande pour idempotence detection
    existing_count = await instant_match_service._count_instant_today(
        user.id, "scanner", db,
    )

    match, target = await instant_match_service.create_instant_match(
        scanner=user,
        scanned_qr_token=body.scanned_qr_token,
        scanner_lat=body.scanner_lat,
        scanner_lng=body.scanner_lng,
        event_id=body.event_id,
        db=db,
    )
    # is_idempotent = on a renvoyé un match existant (le compteur n'a pas bougé).
    is_idempotent = await instant_match_service._count_instant_today(
        user.id, "scanner", db,
    ) == existing_count

    return {
        "match_id": match.id,
        "other_user_id": target.id,
        "other_display_name": target.profile.display_name if target.profile else "",
        "icebreaker": instant_match_service.build_icebreaker(body.event_id),
        "is_idempotent": is_idempotent,
        "target_verified": bool(target.is_selfie_verified),
    }


# /likes-received doit être déclarée AVANT /{match_id} pour éviter que
# FastAPI ne matche "likes-received" comme un UUID.
@router.get("/likes-received", response_model=LikesReceivedResponse)
async def likes_received(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    return await feed_service.get_likes_received(
        user, db, redis, lang=detect_lang(request)
    )


@router.get("/seen-irl", response_model=SeenIrlResponse)
async def get_seen_irl(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Liste les users que l'appelant a croisés à un event vérifié dans les
    3 derniers jours, et avec qui il n'est pas encore en Match.

    Le mobile affiche cette liste dans MatchesScreen (section "Seen IRL")
    avec un CTA "lance une flamme" pour aller liker leur profil.
    """
    items = await seen_irl_service.list_seen_irl(user, db)
    return {"items": items}


@router.get("/{match_id}", response_model=MatchDetailResponse)
async def match_detail(
    match_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await match_service.get_match_detail(user, match_id, db)


@router.get("/{match_id}/context")
async def match_context(
    match_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Renvoie le contexte du match (event/quartier/spot/tags/instant_qr).
    Utilisé par mobile pour afficher ChatContextHeader au-dessus de
    chaque conversation.
    """
    from app.services import match_context_service
    return await match_context_service.get_match_context(
        match_id, user, db, lang=detect_lang(request),
    )


@router.delete("/{match_id}", response_model=UnmatchResponse)
async def delete_match(
    match_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await match_service.unmatch(user, match_id, db)


__all__ = ["router"]
