from __future__ import annotations

"""
Celery tasks — pipeline de moderation photo (§16.1b, §10, S13).

moderate_photo_onnx : pipeline 6 checks dans l'ordre :
  1. EXIF authenticity (toujours, meme sans modeles ONNX)
  2. NSFW detection (si modele dispo)
  3. Face detection (si modele dispo)
  4. Selfie ↔ photo comparison (face_service)
  5. Genre consistency (face_service)
  6. Temporal diversity (toujours)

Decision finale :
  - NSFW > 0.7 → rejected direct (seul cas d'auto-reject)
  - Un check avec risk > 0.7 → manual_review
  - Somme des risks > 1.0 → manual_review
  - Face mismatch < 0.3 → flag + notification admin
  - Sinon → approved

Chaque check qui skip (modele absent) NE BLOQUE PAS le pipeline.
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.core.config import get_settings
from app.db.session import async_session
from app.models.photo import Photo
from app.services.face_verification_service import (
    check_photo_authenticity,
    check_photo_temporal_diversity,
    face_service,
)
from app.services.photo_service import get_photo_disk_path

log = structlog.get_logger()
settings = get_settings()


async def _moderate_photo_onnx_async(photo_id_str: str) -> dict:
    """Pipeline 6 checks. Met a jour Photo en base."""
    photo_id = UUID(photo_id_str)

    async with async_session() as db:
        photo = await db.get(Photo, photo_id)
        if photo is None:
            return {"status": "not_found"}
        if photo.moderation_status != "pending":
            return {"status": "already_processed"}

        disk_path = get_photo_disk_path(photo)
        checks: dict[str, dict] = {}
        total_risk = 0.0
        decision = "approved"

        # ── 1. EXIF authenticity (toujours) ──
        exif_result = check_photo_authenticity(disk_path)
        checks["exif"] = exif_result
        total_risk += exif_result.get("risk", 0.0)

        # ── 2. NSFW detection (si modele dispo) ──
        nsfw_path = Path(settings.nsfw_model_path)
        if nsfw_path.exists():
            try:
                import onnxruntime as ort
                from PIL import Image
                import numpy as np

                sess = ort.InferenceSession(str(nsfw_path))
                img = Image.open(disk_path).convert("RGB").resize((224, 224))
                arr = np.array(img, dtype=np.float32) / 255.0
                arr = arr.transpose(2, 0, 1)[np.newaxis, ...]
                inp = sess.get_inputs()[0].name
                out = sess.run(None, {inp: arr})
                score = float(out[0][0][0]) if out else 0.0
                checks["nsfw"] = {"score": score}
                if score > settings.nsfw_threshold_reject:
                    decision = "rejected"
                elif score > settings.nsfw_threshold_review:
                    total_risk += score
            except Exception as e:
                log.warning("nsfw_check_error", error=str(e))
                checks["nsfw"] = {"status": "skip", "reason": str(e)}
        else:
            checks["nsfw"] = {"status": "skip", "reason": "model_not_found"}

        # ── 3. Face detection (YuNet via OpenCV) ──
        detected = face_service.detect_faces(disk_path)
        if detected is None or face_service._yunet_detector is None:
            checks["face_detection"] = {
                "status": "skip",
                "reason": "model_not_found",
            }
        elif len(detected) == 0:
            checks["face_detection"] = {
                "status": "no_face",
                "risk": 0.3,
            }
            total_risk += 0.3
        elif len(detected) == 1:
            checks["face_detection"] = {
                "status": "ok",
                "confidence": detected[0]["confidence"],
            }
        else:
            checks["face_detection"] = {
                "status": "multiple_faces",
                "count": len(detected),
                "risk": 0.1,
            }
            total_risk += 0.1

        # ── 4. Selfie ↔ photo comparison ──
        selfie_result = await face_service.verify_photo_against_selfie(
            photo.user_id, disk_path, db
        )
        checks["selfie_compare"] = selfie_result
        if selfie_result.get("status") == "clear_mismatch":
            total_risk += 0.8
        elif selfie_result.get("status") == "mismatch":
            total_risk += 0.5

        # ── 5. Genre consistency ──
        gender_result = await face_service.verify_gender_consistency(
            photo.user_id, db
        )
        checks["gender"] = gender_result

        # ── 6. Temporal diversity (toujours) ──
        user_photos = (
            await db.execute(
                select(Photo).where(
                    Photo.user_id == photo.user_id,
                    Photo.is_deleted.is_(False),
                )
            )
        ).scalars().all()
        if len(user_photos) >= 3:
            temporal_result = check_photo_temporal_diversity(list(user_photos))
            checks["temporal"] = temporal_result
            total_risk += temporal_result.get("risk", 0.0)
        else:
            checks["temporal"] = {
                "status": "skip",
                "reason": "fewer_than_3_photos",
            }

        # ── Decision finale ──
        if decision != "rejected":
            if total_risk > 1.0:
                decision = "manual_review"
            elif any(
                c.get("risk", 0) > 0.7
                for c in checks.values()
                if isinstance(c.get("risk"), (int, float))
            ):
                decision = "manual_review"
            elif selfie_result.get("action") == "flag_and_notify_admin":
                decision = "manual_review"

        photo.moderation_status = decision
        photo.moderation_score = round(total_risk, 3)
        photo.rejection_reason = json.dumps(checks, default=str)
        await db.commit()

        log.info(
            "photo_moderated_onnx",
            photo_id=photo_id_str,
            decision=decision,
            total_risk=total_risk,
        )
        return {"status": decision, "checks": checks}


@celery_app.task(name="app.tasks.photo_tasks.moderate_photo_onnx")
def moderate_photo_onnx(photo_id: str) -> dict:
    return asyncio.run(_moderate_photo_onnx_async(photo_id))


# Task external : appelle Sightengine ou Google Vision.
# Stub pour l'instant — sera implemente quand on active le mode external.
@celery_app.task(name="app.tasks.photo_tasks.moderate_photo_external")
def moderate_photo_external(photo_id: str) -> dict:
    log.info("photo_task_external_stub", photo_id=photo_id)
    return {"status": "stub"}


__all__ = ["moderate_photo_onnx", "moderate_photo_external"]
