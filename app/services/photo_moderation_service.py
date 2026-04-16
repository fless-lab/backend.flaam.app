from __future__ import annotations

"""
Photo moderation service — §16.1b.

Dispatcher 4 modes switchables par ENV (`PHOTO_MODERATION_MODE`) :
  - `manual`   : no-op, la photo reste "pending" jusqu'à action admin
  - `onnx`     : route vers Celery task `moderate_photo_onnx` (S11 wiring)
  - `external` : route vers Celery task `moderate_photo_external` (S11 wiring)
  - `off`      : auto-approve (tests/dev uniquement)

Le pipeline est appelé directement après l'upload (sync) pour rester
simple en MVP. Quand Celery sera câblé en S11, les modes onnx/external
délégueront via `.delay()` (déjà prêt).

Résultats possibles :
  - approved      : photo OK, visible dans le profil
  - manual_review : fallback admin (NSFW zone grise, pas de face)
  - rejected      : NSFW > seuil
"""

from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.photo import Photo

log = structlog.get_logger()
settings = get_settings()


VALID_STATUSES = {"pending", "approved", "manual_review", "rejected", "deleted"}


async def moderate_photo(photo_id: UUID, db: AsyncSession) -> str:
    """
    Point d'entrée unique. Retourne le nouveau moderation_status.
    Idempotent : si la photo n'est plus pending, on ne fait rien.
    """
    photo = await db.get(Photo, photo_id)
    if photo is None:
        log.warning("moderation_photo_not_found", photo_id=str(photo_id))
        return "not_found"
    if photo.moderation_status != "pending":
        return photo.moderation_status

    mode = settings.photo_moderation_mode

    if mode == "off":
        photo.moderation_status = "approved"
        log.info("photo_auto_approved", photo_id=str(photo_id))
        return "approved"

    if mode == "manual":
        # Rien à faire : la photo reste pending jusqu'à action admin.
        return "pending"

    if mode == "onnx":
        # Import paresseux : la task Celery ne doit pas être chargée en
        # environnement de test (pas de broker).
        try:
            from app.tasks.photo_tasks import moderate_photo_onnx

            moderate_photo_onnx.delay(str(photo_id))
        except Exception as exc:
            log.warning(
                "photo_moderation_onnx_enqueue_failed",
                photo_id=str(photo_id),
                error=str(exc),
            )
        return "pending"

    if mode == "external":
        try:
            from app.tasks.photo_tasks import moderate_photo_external

            moderate_photo_external.delay(str(photo_id))
        except Exception as exc:
            log.warning(
                "photo_moderation_external_enqueue_failed",
                photo_id=str(photo_id),
                error=str(exc),
            )
        return "pending"

    raise ValueError(f"Unknown PHOTO_MODERATION_MODE: {mode}")


__all__ = ["moderate_photo", "VALID_STATUSES"]
