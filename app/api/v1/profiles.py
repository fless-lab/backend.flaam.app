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

import os
from uuid import UUID

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import FlaamError
from app.core.i18n import detect_lang

from app.core.config import get_settings
from app.core.dependencies import get_current_user, get_db, get_redis
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
from app.services import export_service, feed_service, photo_service, profile_service

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


@router.put("/me")
async def update_me(
    body: UpdateProfileBody,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    old_step = user.onboarding_step
    payload = body.model_dump(exclude_unset=True)
    result = await profile_service.update_profile(
        user, payload, db, lang=detect_lang(request)
    )
    # Invalidate city feeds if profile data affecting matching changed
    # Champs qui invalident le feed : rhythm retiré, languages = display only.
    feed_fields = {"intention", "sector", "seeking"}
    just_completed = (
        user.onboarding_step == "completed" and old_step != "completed"
    )
    if (user.city_id and feed_fields & payload.keys()) or just_completed:
        await feed_service.invalidate_city_feeds(user.city_id, db, redis)
    if just_completed:
        await _ensure_invite_code(user, db)
    return result


@router.patch("/me")
async def patch_me(
    body: UpdateProfileBody,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    """Mise à jour partielle (onboarding step-by-step). Pas de champs requis."""
    old_step = user.onboarding_step
    payload = body.model_dump(exclude_unset=True)
    result = await profile_service.patch_profile(
        user, payload, db, lang=detect_lang(request)
    )
    # Champs qui invalident le feed : rhythm retiré, languages = display only.
    feed_fields = {"intention", "sector", "seeking"}
    just_completed = (
        user.onboarding_step == "completed" and old_step != "completed"
    )
    if (user.city_id and feed_fields & payload.keys()) or just_completed:
        await feed_service.invalidate_city_feeds(user.city_id, db, redis)
    if just_completed:
        await _ensure_invite_code(user, db)
    return result


async def _ensure_invite_code(user, db) -> None:
    """Génère un code d'invitation au passage onboarding → completed.

    Best-effort : si la génération échoue (quota=0, etc.) on n'interrompt
    pas la complétion d'onboarding. L'user pourra toujours en générer un
    via Settings.
    """
    try:
        from app.services import invite_service
        await invite_service.generate_codes(user, db)
    except Exception:
        pass


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

    Pipeline (quand modèles ONNX dispo en prod) :
      1. Sauvegarde la photo
      2. YuNet détecte les visages → exactement 1 visage avec confidence
         >= 0.8 ; sinon rejet 422
      3. ArcFace extrait l'embedding 128D (sera comparé aux photos non-
         selfie pour empêcher les uploads d'autres personnes)
      4. is_selfie_verified = True

    En dev (modèle absent ou face_verification_enabled=False) : on accepte
    sans détection mais on log explicitement (selfie_verified_dev_bypass).
    """
    from app.services.face_verification_service import face_service
    from app.services.photo_service import get_photo_disk_path

    photo = await photo_service.upload_photo(
        user, file, display_order=None, db=db, is_selfie=True,
    )

    verification_passed = False
    verification_reason = "dev_bypass"

    if settings.face_verification_enabled:
        face_service._ensure_loaded()
        if face_service._session is None:
            log.warning(
                "selfie_verified_dev_bypass",
                user_id=str(user.id),
                reason="face_model_not_loaded",
            )
        else:
            disk_path = get_photo_disk_path(photo)
            faces = face_service.detect_faces(disk_path)
            high_conf = [f for f in faces if f["confidence"] >= 0.8]
            if len(high_conf) == 0:
                from app.core.exceptions import AppException
                from fastapi import status as _status
                raise AppException(
                    _status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "selfie_no_face_detected",
                )
            if len(high_conf) > 1:
                from app.core.exceptions import AppException
                from fastapi import status as _status
                raise AppException(
                    _status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "selfie_multiple_faces",
                )
            embedding = face_service.embed_face(disk_path)
            if embedding is None:
                from app.core.exceptions import AppException
                from fastapi import status as _status
                raise AppException(
                    _status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "selfie_embedding_failed",
                )
            verification_passed = True
            verification_reason = f"face_match_confidence={high_conf[0]['confidence']:.2f}"
    else:
        log.warning(
            "selfie_verified_dev_bypass",
            user_id=str(user.id),
            reason="face_verification_disabled",
        )

    photo.is_verified_selfie = True
    user.is_selfie_verified = True
    advance_onboarding(user)
    await db.commit()
    await db.refresh(photo)
    log.info(
        "selfie_verified",
        user_id=str(user.id),
        photo_id=str(photo.id),
        passed_real_check=verification_passed,
        reason=verification_reason,
    )
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


# ── Export RGPD (§17) ──────────���──────────────────────────────────

@router.get("/me/export")
async def export_my_data(
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> FileResponse:
    """
    Exporte toutes les donnees personnelles de l'utilisateur (RGPD Art. 20).
    Rate limit : 1 export par 24h.
    """
    lang = detect_lang(request)
    rate_key = f"export:rate:{user.id}"
    if await redis.exists(rate_key):
        raise FlaamError("export_rate_limited", 429, lang)

    zip_path = await export_service.generate_user_export(user.id, db)
    await redis.set(rate_key, "1", ex=86400)

    background_tasks.add_task(os.remove, zip_path)

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"flaam_export_{user.id}.zip",
    )


__all__ = ["router"]
