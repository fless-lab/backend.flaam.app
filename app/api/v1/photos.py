from __future__ import annotations

"""Routes Photos (§5.3)."""

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.core.rate_limiter import rate_limit
from app.models.user import User
from app.schemas.photos import PhotoReorderBody, PhotoResponse, PhotoSwapBody
from app.services import photo_service

router = APIRouter(prefix="/photos", tags=["photos"])


def _photo_dict(p) -> dict:
    return {
        "id": p.id,
        "original_url": p.original_url,
        "thumbnail_url": p.thumbnail_url,
        "medium_url": p.medium_url,
        "display_order": p.display_order,
        "moderation_status": p.moderation_status,
        "width": p.width,
        "height": p.height,
        "file_size_bytes": p.file_size_bytes,
        "is_verified_selfie": p.is_verified_selfie,
        "dominant_color": p.dominant_color,
    }


@router.post(
    "",
    response_model=PhotoResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit(max_requests=10, window_seconds=86400, name="photo_upload"))],
)
async def upload(
    file: UploadFile = File(...),
    display_order: int | None = Form(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    photo = await photo_service.upload_photo(user, file, display_order, db)
    return _photo_dict(photo)


@router.delete("/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    photo_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await photo_service.delete_photo(user, photo_id, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/reorder", response_model=list[PhotoResponse])
async def reorder(
    body: PhotoReorderBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    photos = await photo_service.reorder_photos(user, body.order, db)
    return [_photo_dict(p) for p in photos]


@router.patch("/swap", response_model=list[PhotoResponse])
async def swap(
    body: PhotoSwapBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    photos = await photo_service.swap_photos(
        user, body.photo_id_a, body.photo_id_b, db,
    )
    return [_photo_dict(p) for p in photos]


__all__ = ["router"]
