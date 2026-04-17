from __future__ import annotations

"""
Face verification service (§10, safety anti-fraud, S13).

Lazy-loading : les modeles ONNX sont charges au premier appel, PAS au
demarrage de l'app. Si le fichier modele n'existe pas au path configure
→ log warning + toutes les fonctions retournent
  {"status": "skip", "reason": "model_not_loaded"}.

Pipeline :
- embed_face()                      : extrait un embedding 128D
- verify_photo_against_selfie()     : cosine similarity selfie ↔ photo
- verify_gender_consistency()       : skip au MVP (modele GenderAge absent)
- check_photo_authenticity()        : analyse EXIF (toujours dispo, pas ONNX)
- check_photo_temporal_diversity()  : memes dates EXIF → flag

Seuils (§10.2) :
    >= 0.7 → match
    0.5-0.7 → warning (log)
    0.3-0.5 → mismatch (flag_for_review)
    < 0.3 → clear_mismatch (flag + admin)

IMPORTANT ETHIQUE :
- Le genre n'est JAMAIS auto-rejete. Mismatch → review HUMAINE.
- Les personnes trans/NB sont bienvenues. Pas de discrimination.
"""

from datetime import datetime
from pathlib import Path
from uuid import UUID

import numpy as np
import structlog
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.photo import Photo
from app.services.photo_service import get_photo_disk_path

log = structlog.get_logger()
settings = get_settings()

# Seuils cosine similarity
MATCH_THRESHOLD = 0.7
WARNING_THRESHOLD = 0.5
MISMATCH_THRESHOLD = 0.3


class FaceVerificationService:

    def __init__(self) -> None:
        self._session = None
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Lazy load : charge le modele au premier appel."""
        if self._loaded:
            return
        self._loaded = True
        if not settings.face_verification_enabled:
            log.info("face_verification_disabled")
            return
        model_path = Path(settings.face_verification_model_path)
        if not model_path.exists():
            log.warning("face_model_not_found", path=str(model_path))
            return
        try:
            import onnxruntime as ort

            self._session = ort.InferenceSession(str(model_path))
            log.info("face_model_loaded", path=str(model_path))
        except Exception as e:
            log.error("face_model_load_error", error=str(e))

    def embed_face(self, image_path: str) -> np.ndarray | None:
        """
        Extrait embedding 128D du visage principal dans l'image.

        Pre-processing :
          1. Lire l'image avec PIL
          2. Resize 112x112 (taille attendue par ArcFace)
          3. Convertir en RGB
          4. Normaliser pixels [-1, 1] : (pixel / 127.5) - 1.0
          5. Transpose en NCHW : (1, 3, 112, 112) float32

        Retourne None si modele absent ou erreur.
        """
        self._ensure_loaded()
        if self._session is None:
            return None
        try:
            img = Image.open(image_path).convert("RGB").resize((112, 112))
            arr = np.array(img, dtype=np.float32)
            arr = (arr / 127.5) - 1.0
            # HWC → NCHW
            arr = arr.transpose(2, 0, 1)[np.newaxis, ...]
            input_name = self._session.get_inputs()[0].name
            outputs = self._session.run(None, {input_name: arr})
            embedding = outputs[0][0]
            # L2-normaliser
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = embedding / norm
            return embedding
        except Exception as e:
            log.warning("embed_face_error", path=image_path, error=str(e))
            return None

    async def verify_photo_against_selfie(
        self, user_id: UUID, photo_path: str, db: AsyncSession
    ) -> dict:
        """
        Compare le selfie verifie du user avec une photo uploadee.
        """
        self._ensure_loaded()
        if self._session is None:
            return {"status": "skip", "reason": "model_not_loaded"}

        # Charger le selfie
        result = await db.execute(
            select(Photo).where(
                Photo.user_id == user_id,
                Photo.is_verified_selfie.is_(True),
                Photo.is_deleted.is_(False),
            )
        )
        selfie = result.scalar_one_or_none()
        if selfie is None:
            return {"status": "skip", "reason": "no_verified_selfie"}

        selfie_path = get_photo_disk_path(selfie)
        selfie_emb = self.embed_face(selfie_path)
        photo_emb = self.embed_face(photo_path)

        if selfie_emb is None or photo_emb is None:
            return {"status": "skip", "reason": "embedding_failed"}

        similarity = float(np.dot(selfie_emb, photo_emb))

        if similarity >= MATCH_THRESHOLD:
            return {"status": "match", "similarity": similarity}
        if similarity >= WARNING_THRESHOLD:
            return {
                "status": "warning",
                "similarity": similarity,
                "action": "log",
            }
        if similarity >= MISMATCH_THRESHOLD:
            return {
                "status": "mismatch",
                "similarity": similarity,
                "action": "flag_for_review",
            }
        return {
            "status": "clear_mismatch",
            "similarity": similarity,
            "action": "flag_and_notify_admin",
        }

    async def verify_gender_consistency(
        self, user_id: UUID, db: AsyncSession
    ) -> dict:
        """
        Compare le genre detecte sur le selfie avec le genre declare.

        Au MVP : le modele GenderAge n'est pas installe separement
        (ArcFace pur ne predit pas le genre). On skip.

        ETHIQUE : mismatch → review HUMAINE, JAMAIS auto-reject.
        """
        return {"status": "skip", "reason": "gender_model_not_available"}


# Singleton module-level
face_service = FaceVerificationService()


# ══════════════════════════════════════════════════════════════════════
# EXIF checks (pas de modele ONNX requis)
# ══════════════════════════════════════════════════════════════════════

_AI_KEYWORDS = [
    "photoshop", "midjourney", "stable diffusion",
    "dall-e", "comfyui", "gimp", "canva",
    "runway", "leonardo", "firefly",
]

_SUSPICIOUS_SQUARE_SIZES = {256, 512, 768, 1024, 2048}


def check_photo_authenticity(file_path: str) -> dict:
    """
    Analyse les metadonnees EXIF pour detecter les photos suspectes.
    Retourne {"flags": [...], "risk": 0.0-1.0}
    """
    try:
        img = Image.open(file_path)
    except Exception:
        return {"flags": ["unreadable_image"], "risk": 1.0}

    flags: list[str] = []

    exif = None
    if hasattr(img, "_getexif"):
        try:
            exif = img._getexif()
        except Exception:
            pass

    if exif is None:
        flags.append("no_exif_data")
    else:
        # 272 = Model (camera model)
        if 272 not in exif:
            flags.append("no_camera_model")
        # 36867 = DateTimeOriginal
        if 36867 not in exif:
            flags.append("no_date")
        # 305 = Software
        software = str(exif.get(305, "")).lower()
        if any(k in software for k in _AI_KEYWORDS):
            flags.append("ai_or_editor_software")

    # Resolution carree suspecte (typique AI)
    w, h = img.size
    if w == h and w in _SUSPICIOUS_SQUARE_SIZES:
        flags.append("square_suspicious_resolution")

    risk = min(1.0, len(flags) * 0.2)
    return {"flags": flags, "risk": risk}


def check_photo_temporal_diversity(user_photos: list[Photo]) -> dict:
    """
    Verifie que les photos n'ont pas toutes ete prises le meme jour.
    Appele quand 3+ photos existent pour un user.
    """
    dates: list[datetime] = []
    for photo in user_photos:
        try:
            path = get_photo_disk_path(photo)
            img = Image.open(path)
            exif = img._getexif() if hasattr(img, "_getexif") else None
            if exif and 36867 in exif:
                dt = datetime.strptime(exif[36867], "%Y:%m:%d %H:%M:%S")
                dates.append(dt.date())
        except Exception:
            continue

    if len(dates) < 3:
        return {"status": "insufficient_data", "risk": 0.0}

    unique_days = len(set(dates))

    if unique_days == 1 and len(dates) >= 4:
        return {
            "status": "all_same_day",
            "risk": 0.3,
            "action": "flag_for_review",
        }
    if unique_days <= 2 and len(dates) >= 5:
        return {
            "status": "very_low_diversity",
            "risk": 0.4,
            "action": "flag_for_review",
        }

    return {"status": "diverse", "risk": 0.0}


__all__ = [
    "face_service",
    "FaceVerificationService",
    "check_photo_authenticity",
    "check_photo_temporal_diversity",
    "MATCH_THRESHOLD",
    "WARNING_THRESHOLD",
    "MISMATCH_THRESHOLD",
]
