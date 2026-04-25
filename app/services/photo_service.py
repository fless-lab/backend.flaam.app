from __future__ import annotations

"""
Photo service — §5.3, §3.7.

Pipeline d'upload :
1. Lecture + validation taille / format
2. content_hash SHA-256 (détection doublons inter-profils — spec §3.7)
3. Génération de 3 variants WebP :
   - original : max 1200px côté long
   - medium   : max 600px côté long
   - thumbnail: 150x150, crop carré centré
4. Extraction de la couleur dominante (placeholder mobile §30)
5. Sauvegarde disque (STORAGE_ROOT/{user_id}/{photo_id}_{variant}.webp)
6. Création de la row Photo (moderation_status="pending" — le pipeline
   de modération tournera en Session 10)

Les URLs retournées sont construites à partir de PUBLIC_BASE_URL.

Le stockage local sera remplacé par Cloudflare R2 en Session 11 ;
cette couche est volontairement minimale pour faciliter la bascule.
"""

import hashlib
import io
import uuid
from pathlib import Path
from uuid import UUID

import structlog
from fastapi import UploadFile, status
from PIL import Image, ImageFilter, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.core.onboarding import advance_onboarding, compute_completeness
from app.models.photo import Photo
from app.models.user import User
from app.services import photo_moderation_service


async def _fetch_user_photos(user: User, db: AsyncSession) -> list[Photo]:
    """
    Source de vérité côté DB (plutôt que `user.photos`), pour ne pas
    dépendre de la fraîcheur de la relation dans la session courante.
    """
    result = await db.execute(
        select(Photo).where(Photo.user_id == user.id).order_by(Photo.display_order)
    )
    return list(result.scalars().all())

log = structlog.get_logger()
settings = get_settings()


ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "MPO"}  # MPO = iPhone HDR (traité comme JPEG)
ORIGINAL_MAX_SIDE = 1200
MEDIUM_MAX_SIDE = 600
THUMBNAIL_SIDE = 150
BLUR_SIDE = 200
BLUR_RADIUS = 30
WEBP_QUALITY = 85


# ── Helpers ──────────────────────────────────────────────────────────

def _storage_dir(user_id: UUID) -> Path:
    path = Path(settings.storage_root) / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _blurred_dir() -> Path:
    """Anonymized blur directory — no user_id in path."""
    path = Path(settings.storage_root) / "blurred"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _public_url(user_id: UUID, filename: str) -> str:
    return f"/uploads/{user_id}/{filename}"


def _blurred_public_url(filename: str) -> str:
    """URL without user_id so it can't be traced back."""
    return f"/uploads/blurred/{filename}"


def _scale_to_max(img: Image.Image, max_side: int) -> Image.Image:
    w, h = img.size
    longest = max(w, h)
    if longest <= max_side:
        return img.copy()
    ratio = max_side / longest
    return img.resize((int(w * ratio), int(h * ratio)), Image.Resampling.LANCZOS)


def _center_square_crop(img: Image.Image, side: int) -> Image.Image:
    w, h = img.size
    s = min(w, h)
    left = (w - s) // 2
    top = (h - s) // 2
    cropped = img.crop((left, top, left + s, top + s))
    return cropped.resize((side, side), Image.Resampling.LANCZOS)


def _extract_dominant_color(img: Image.Image) -> str:
    """
    Quantize à 1 couleur sur une version miniature pour trouver la
    couleur dominante. Retourne un hex "#RRGGBB".
    """
    small = img.copy()
    small.thumbnail((50, 50), Image.Resampling.LANCZOS)
    if small.mode != "RGB":
        small = small.convert("RGB")
    quant = small.quantize(colors=1)
    palette = quant.getpalette() or [0, 0, 0]
    r, g, b = palette[0], palette[1], palette[2]
    return f"#{r:02x}{g:02x}{b:02x}"


def _generate_blur(img: Image.Image, side: int = BLUR_SIDE) -> Image.Image:
    """
    Generate a heavily blurred square image for free-tier likes preview.

    The blur is strong enough that the person is unrecognizable (silhouette
    only) but the overall color palette is preserved for visual appeal.
    Stored in /uploads/blurred/ (no user_id in path) to prevent identification.
    """
    # Center-crop to square, resize down, then blur aggressively
    w, h = img.size
    s = min(w, h)
    left = (w - s) // 2
    top = (h - s) // 2
    cropped = img.crop((left, top, left + s, top + s))
    small = cropped.resize((side, side), Image.Resampling.LANCZOS)
    return small.filter(ImageFilter.GaussianBlur(radius=BLUR_RADIUS))


def _save_webp(img: Image.Image, path: Path) -> int:
    """Sauvegarde en WebP et retourne la taille fichier."""
    img.save(path, format="WEBP", quality=WEBP_QUALITY, method=4)
    return path.stat().st_size


# ── Upload ───────────────────────────────────────────────────────────

async def upload_photo(
    user: User,
    file: UploadFile,
    display_order: int | None,
    db: AsyncSession,
) -> Photo:
    current_photos = await _fetch_user_photos(user, db)
    max_count = (
        settings.photo_max_count_premium
        if user.is_premium
        else settings.photo_max_count_free
    )
    if len(current_photos) >= max_count:
        raise AppException(
            status.HTTP_400_BAD_REQUEST,
            f"max_photos_reached:{max_count}",
        )

    raw = await file.read()
    size_bytes = len(raw)
    if size_bytes == 0:
        raise AppException(status.HTTP_400_BAD_REQUEST, "empty_file")
    if size_bytes > settings.photo_max_size_bytes:
        raise AppException(
            status.HTTP_400_BAD_REQUEST,
            f"file_too_large:{settings.photo_max_size_bytes}",
        )

    try:
        source = Image.open(io.BytesIO(raw))
        source.load()  # force décode pour attraper les fichiers corrompus
    except (UnidentifiedImageError, OSError):
        raise AppException(status.HTTP_400_BAD_REQUEST, "invalid_image")

    if source.format not in ALLOWED_FORMATS:
        raise AppException(
            status.HTTP_400_BAD_REQUEST,
            f"unsupported_format:{source.format}",
        )

    if source.mode not in ("RGB", "RGBA"):
        source = source.convert("RGB")

    content_hash = hashlib.sha256(raw).hexdigest()

    # Position : par défaut à la fin ; si fournie, clamp dans [0, count]
    count = len(current_photos)
    if display_order is None:
        target_order = count
    else:
        if display_order < 0 or display_order > count:
            raise AppException(
                status.HTTP_400_BAD_REQUEST,
                f"invalid_display_order:{display_order}",
            )
        target_order = display_order
        # Décaler les photos >= target_order
        for p in current_photos:
            if p.display_order >= target_order:
                p.display_order += 1

    photo_id = uuid.uuid4()
    dir_ = _storage_dir(user.id)
    blur_dir = _blurred_dir()
    original_path = dir_ / f"{photo_id}_original.webp"
    medium_path = dir_ / f"{photo_id}_medium.webp"
    thumb_path = dir_ / f"{photo_id}_thumb.webp"
    blur_path = blur_dir / f"{photo_id}_blur.webp"

    try:
        original_img = _scale_to_max(source, ORIGINAL_MAX_SIDE)
        medium_img = _scale_to_max(source, MEDIUM_MAX_SIDE)
        thumb_img = _center_square_crop(source, THUMBNAIL_SIDE)
        blur_img = _generate_blur(source)

        original_size = _save_webp(original_img, original_path)
        _save_webp(medium_img, medium_path)
        _save_webp(thumb_img, thumb_path)
        _save_webp(blur_img, blur_path)

        dominant = _extract_dominant_color(source)
    except Exception:
        # Clean up tout fichier créé avant remontée
        for p in (original_path, medium_path, thumb_path, blur_path):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    photo = Photo(
        id=photo_id,
        user_id=user.id,
        original_url=_public_url(user.id, original_path.name),
        thumbnail_url=_public_url(user.id, thumb_path.name),
        medium_url=_public_url(user.id, medium_path.name),
        blurred_url=_blurred_public_url(blur_path.name),
        display_order=target_order,
        content_hash=content_hash,
        width=original_img.width,
        height=original_img.height,
        file_size_bytes=original_size,
        moderation_status="pending",
        dominant_color=dominant,
    )
    db.add(photo)

    # Recompute completeness (la modif du profil dépend du nombre de photos)
    if user.profile is not None:
        # On pousse vers la relation chargée (utile si le même User est
        # réutilisé dans la session) et on recalcule le score.
        try:
            user.photos.append(photo)
        except Exception:
            pass
        score, _ = compute_completeness(user, user.profile)
        user.profile.profile_completeness = score

    # Avance l'onboarding (ex. PHOTOS done à partir de 3 photos)
    advance_onboarding(user)

    await db.commit()
    await db.refresh(photo)

    # Pipeline modération (§16.1b). Appel direct sync : le dispatcher
    # décide du mode (manual/onnx/external/off). Les modes asynchrones
    # enqueuent une task Celery ; "manual" est no-op.
    await photo_moderation_service.moderate_photo(photo.id, db)
    await db.commit()
    await db.refresh(photo)

    log.info(
        "photo_uploaded",
        user_id=str(user.id),
        photo_id=str(photo.id),
        size=size_bytes,
        hash=content_hash[:12],
        moderation_status=photo.moderation_status,
    )
    return photo


# ── Delete ───────────────────────────────────────────────────────────

async def delete_photo(user: User, photo_id: UUID, db: AsyncSession) -> None:
    current = await _fetch_user_photos(user, db)
    target = next((p for p in current if p.id == photo_id), None)
    if target is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "photo_not_found")

    if len(current) <= settings.photo_min_count:
        raise AppException(
            status.HTTP_400_BAD_REQUEST,
            f"min_photos_required:{settings.photo_min_count}",
        )

    # Supprimer les fichiers disque (best-effort)
    _unlink_photo_files(user.id, photo_id)

    # Resserrer les display_order restants (0..n-1, stable)
    remaining = [p for p in current if p.id != photo_id]
    remaining.sort(key=lambda p: p.display_order)
    for idx, p in enumerate(remaining):
        p.display_order = idx

    await db.delete(target)

    if user.profile is not None:
        # Le compteur côté relation sera rafraîchi après le commit ; on
        # calcule directement à partir de `remaining` pour rester cohérent.
        if user.photos is not None:
            try:
                user.photos.remove(target)
            except ValueError:
                pass
        score, _ = compute_completeness(user, user.profile)
        user.profile.profile_completeness = score

    # delete ne peut pas faire régresser l'onboarding (on refuse si <3),
    # mais on ré-aligne au cas où l'état serait désynchronisé.
    advance_onboarding(user)

    await db.commit()
    log.info("photo_deleted", user_id=str(user.id), photo_id=str(photo_id))


def _unlink_photo_files(user_id: UUID, photo_id: UUID) -> None:
    dir_ = Path(settings.storage_root) / str(user_id)
    for variant in ("original", "medium", "thumb"):
        path = dir_ / f"{photo_id}_{variant}.webp"
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    # Blur lives in the anonymized directory
    blur_path = Path(settings.storage_root) / "blurred" / f"{photo_id}_blur.webp"
    try:
        blur_path.unlink(missing_ok=True)
    except OSError:
        pass


# ── Reorder ──────────────────────────────────────────────────────────

async def reorder_photos(
    user: User, new_order: list[UUID], db: AsyncSession
) -> list[Photo]:
    result = await db.execute(select(Photo).where(Photo.user_id == user.id))
    photos = list(result.scalars().all())

    if len(photos) != len(new_order):
        raise AppException(
            status.HTTP_400_BAD_REQUEST,
            f"order_length_mismatch:{len(new_order)}/{len(photos)}",
        )

    owned_ids = {p.id for p in photos}
    if set(new_order) != owned_ids:
        raise AppException(
            status.HTTP_400_BAD_REQUEST,
            "order_must_contain_all_photo_ids",
        )

    by_id = {p.id: p for p in photos}
    for new_position, pid in enumerate(new_order):
        by_id[pid].display_order = new_position

    await db.commit()
    return sorted(photos, key=lambda p: p.display_order)


async def swap_photos(
    user: User, photo_id_a: UUID, photo_id_b: UUID, db: AsyncSession
) -> list[Photo]:
    """Swap display_order of two photos owned by the same user."""
    result = await db.execute(select(Photo).where(Photo.user_id == user.id))
    photos = list(result.scalars().all())

    by_id = {p.id: p for p in photos}
    a = by_id.get(photo_id_a)
    b = by_id.get(photo_id_b)
    if a is None or b is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "photo_not_found")

    a.display_order, b.display_order = b.display_order, a.display_order
    await db.commit()
    return sorted(photos, key=lambda p: p.display_order)


def get_photo_disk_path(photo: Photo) -> str:
    """Reconstruit le chemin disque depuis l'URL stockee en BD."""
    # URL format: {public_base_url}/uploads/{user_id}/{filename}
    # On extrait la partie relative après "/uploads/"
    url = photo.original_url
    marker = "/uploads/"
    idx = url.find(marker)
    if idx != -1:
        relative = url[idx + len(marker) :]
    else:
        relative = url.lstrip("/")
    return str(Path(settings.storage_root) / relative)


async def backfill_blurred_photos(db: AsyncSession) -> int:
    """
    Generate blurred variants for all existing photos that don't have one.
    Called once via management command or migration.
    """
    result = await db.execute(
        select(Photo).where(
            Photo.is_deleted.is_(False),
            Photo.blurred_url.is_(None),
        )
    )
    photos = result.scalars().all()
    count = 0
    blur_dir = _blurred_dir()

    for photo in photos:
        disk_path = get_photo_disk_path(photo)
        try:
            source = Image.open(disk_path)
            source.load()
            if source.mode not in ("RGB", "RGBA"):
                source = source.convert("RGB")
            blur_img = _generate_blur(source)
            blur_path = blur_dir / f"{photo.id}_blur.webp"
            _save_webp(blur_img, blur_path)
            photo.blurred_url = _blurred_public_url(blur_path.name)
            count += 1
        except Exception:
            log.warning("backfill_blur_failed", photo_id=str(photo.id))
            continue

    await db.commit()
    log.info("backfill_blurred_photos_done", count=count)
    return count


__all__ = [
    "upload_photo",
    "delete_photo",
    "reorder_photos",
    "get_photo_disk_path",
    "backfill_blurred_photos",
]
