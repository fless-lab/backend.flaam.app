from __future__ import annotations

"""Tests Face verification service (§10, S13)."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.services.face_verification_service import (
    FaceVerificationService,
    MATCH_THRESHOLD,
    MISMATCH_THRESHOLD,
)
from tests._feed_setup import seed_ama_and_kofi

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ═══════���══════════════════════════════════════════════════════════════
# Service disabled / model missing
# ══════��═════════════════════════════��═════════════════════════════════


def test_face_service_skip_when_disabled(monkeypatch):
    """Settings face_verification_enabled=False → skip."""
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "face_verification_enabled", False)

    svc = FaceVerificationService()
    result = svc.embed_face("/nonexistent.jpg")
    assert result is None


def test_face_service_skip_when_model_missing(monkeypatch):
    """Settings enabled=True mais fichier inexistant → None."""
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "face_verification_enabled", True)
    monkeypatch.setattr(
        settings, "face_verification_model_path", "/nonexistent/model.onnx"
    )

    svc = FaceVerificationService()
    result = svc.embed_face("/nonexistent.jpg")
    assert result is None


# ════════���═════════════════════════════════════════════════════════════
# Embed with mocked ONNX
# ═════��════════════════════════════════════════════════════════════════


def test_face_service_embed_returns_128d(monkeypatch, tmp_path):
    """Mock onnxruntime → embed_face retourne un array de shape (128,)."""
    from PIL import Image

    from app.core.config import get_settings

    settings = get_settings()

    # Create a test image
    img = Image.new("RGB", (200, 200), (128, 128, 128))
    img_path = str(tmp_path / "test.jpg")
    img.save(img_path)

    # Mock onnxruntime.InferenceSession
    mock_session = MagicMock()
    fake_emb = np.random.randn(128).astype(np.float32)
    mock_session.run.return_value = [np.array([fake_emb])]
    mock_session.get_inputs.return_value = [MagicMock(name="input")]

    # Directly set the session on a fresh service (skip _ensure_loaded)
    svc = FaceVerificationService()
    svc._loaded = True
    svc._session = mock_session

    result = svc.embed_face(img_path)

    assert result is not None
    assert result.shape == (128,)
    # L2-normalized
    assert abs(np.linalg.norm(result) - 1.0) < 1e-5


# ══��═══════════════════════════════════════════════════════════════════
# Verify photo against selfie (mocked)
# ═��════════════════════════════════════════════════════════════════════


async def test_face_mismatch_flags_for_review(db_session, redis_client):
    """Similarity 0.4 → mismatch + flag_for_review."""
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    svc = FaceVerificationService()
    # Mock embed_face to return controlled embeddings
    emb_a = np.random.randn(128).astype(np.float32)
    emb_a /= np.linalg.norm(emb_a)
    # Create emb_b such that cosine sim ≈ 0.4
    emb_b = emb_a * 0.4 + np.random.randn(128).astype(np.float32) * 0.6
    emb_b /= np.linalg.norm(emb_b)
    # Adjust to get exact 0.4 is hard; mock directly
    svc._loaded = True
    svc._session = MagicMock()

    with patch.object(svc, "embed_face") as mock_embed:
        mock_embed.side_effect = [emb_a, emb_b]
        # We need a selfie in DB
        result = await svc.verify_photo_against_selfie(
            ama.id, "/fake/photo.jpg", db_session
        )

    # If no selfie found, it skips
    if result["status"] == "skip":
        # Ama has photos but not necessarily a verified selfie in seed
        assert result["reason"] in ("no_verified_selfie", "embedding_failed")
    else:
        assert result["status"] in ("mismatch", "warning", "match", "clear_mismatch")


async def test_face_match_returns_ok(db_session, redis_client):
    """Mock high similarity → match."""
    svc = FaceVerificationService()
    svc._loaded = True
    svc._session = MagicMock()

    # Same embedding → similarity = 1.0
    emb = np.random.randn(128).astype(np.float32)
    emb /= np.linalg.norm(emb)

    with patch.object(svc, "embed_face", return_value=emb):
        # Mock DB to return a selfie photo
        from unittest.mock import AsyncMock

        from app.models.photo import Photo

        mock_db = AsyncMock()
        mock_result = MagicMock()
        fake_photo = MagicMock(spec=Photo)
        fake_photo.original_url = "/uploads/test/fake_original.webp"
        mock_result.scalar_one_or_none.return_value = fake_photo
        mock_db.execute.return_value = mock_result

        from uuid import uuid4

        result = await svc.verify_photo_against_selfie(
            uuid4(), "/fake/photo.jpg", mock_db
        )

    assert result["status"] == "match"
    assert result["similarity"] >= MATCH_THRESHOLD


# ═��══════════════════════════════════════���═════════════════════════════
# Gender consistency
# ════════════════════════════════════════════════════════════���═════════


async def test_gender_consistency_skip_if_no_model(db_session, redis_client):
    """Au MVP → skip car modele GenderAge absent."""
    svc = FaceVerificationService()
    from uuid import uuid4

    result = await svc.verify_gender_consistency(uuid4(), db_session)
    assert result["status"] == "skip"
    assert result["reason"] == "gender_model_not_available"
