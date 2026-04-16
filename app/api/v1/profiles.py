from __future__ import annotations

"""
Routes Profiles (§5.2, §13).

- GET /profiles/me
- PUT /profiles/me
- GET /profiles/{user_id}
- POST /profiles/me/selfie (upload selfie de vérification — liveness à venir)
- GET /profiles/me/completeness
- PATCH /profiles/me/visibility (mode pause)
- GET /profiles/me/onboarding
- POST /profiles/me/onboarding/skip
"""

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.i18n import detect_lang

from app.core.config import get_settings
from app.core.dependencies import get_current_user, get_db
from app.core.onboarding import advance_onboarding
from app.models.user import User
from app.schemas.photos import PhotoResponse
from app.schemas.profiles import (
    CompletenessResponse,
    MyProfileResponse,
    OnboardingResponse,
    OnboardingSkipBody,
    OnboardingSkipResponse,
    OtherProfileResponse,
    UpdateProfileBody,
    VisibilityBody,
    VisibilityResponse,
)
from app.services import photo_service, profile_service

log = structlog.get_logger()
settings = get_settings()
router = APIRouter(prefix="/profiles", tags=["profiles"])


# ── Me ───────────────────────────────────────────────────────────────

@router.get("/me", response_model=MyProfileResponse)
async def get_me(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await profile_service.get_my_profile(user, db)


@router.put("/me", response_model=MyProfileResponse)
async def update_me(
    body: UpdateProfileBody,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    payload = body.model_dump(exclude_unset=True)
    return await profile_service.update_profile(
        user, payload, db, lang=detect_lang(request)
    )


@router.get("/me/completeness", response_model=CompletenessResponse)
async def completeness(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await profile_service.calculate_completeness(user, db)


@router.patch("/me/visibility", response_model=VisibilityResponse)
async def visibility(
    body: VisibilityBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await profile_service.toggle_visibility(user, body.is_visible, db)


# ── Onboarding ───────────────────────────────────────────────────────

@router.get("/me/onboarding", response_model=OnboardingResponse)
async def onboarding_state(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await profile_service.get_onboarding_state(user, db)


@router.post("/me/onboarding/skip", response_model=OnboardingSkipResponse)
async def onboarding_skip(
    body: OnboardingSkipBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await profile_service.skip_onboarding_step(user, body.step, db)


# ── Selfie verification ──────────────────────────────────────────────

@router.post("/me/selfie", response_model=PhotoResponse, status_code=201)
async def upload_selfie(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Upload du selfie de vérification.

    La photo reste `moderation_status="pending"` (modération asynchrone
    §17). `is_selfie_verified` passe à True en MVP sans liveness check.

    # TODO: liveness check — Session 11 ou intégration ML Kit (détection
    # de visage + pose aléatoire + anti-spoof). Bascule via le flag
    # `settings.selfie_liveness_required` : quand il passe True, on
    # refuse l'upload tant que le worker n'a pas renvoyé OK.
    """
    if settings.selfie_liveness_required:
        # Garde-fou pour plus tard — on refuse tant que le pipeline n'est
        # pas câblé. Ça permet d'activer le flag en prod dès que le
        # worker de liveness est en place, sans risque de laisser passer
        # des selfies non vérifiés.
        from fastapi import status as _status

        from app.core.exceptions import AppException

        raise AppException(
            _status.HTTP_501_NOT_IMPLEMENTED,
            "liveness_pipeline_not_ready",
        )

    photo = await photo_service.upload_photo(user, file, display_order=None, db=db)
    photo.is_verified_selfie = True
    user.is_selfie_verified = True
    advance_onboarding(user)
    await db.commit()
    await db.refresh(photo)
    log.info("selfie_verified", user_id=str(user.id), photo_id=str(photo.id))
    return {
        "id": photo.id,
        "original_url": photo.original_url,
        "thumbnail_url": photo.thumbnail_url,
        "medium_url": photo.medium_url,
        "display_order": photo.display_order,
        "moderation_status": photo.moderation_status,
        "width": photo.width,
        "height": photo.height,
        "file_size_bytes": photo.file_size_bytes,
        "is_verified_selfie": photo.is_verified_selfie,
        "dominant_color": photo.dominant_color,
    }


# ── Autre utilisateur ────────────────────────────────────────────────

@router.get("/{user_id}", response_model=OtherProfileResponse)
async def get_other(
    user_id: UUID,
    request: Request,
    _me: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await profile_service.get_other_profile(
        user_id, db, lang=detect_lang(request)
    )


__all__ = ["router"]
