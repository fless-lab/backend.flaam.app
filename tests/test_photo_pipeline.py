from __future__ import annotations

"""Tests Photo moderation pipeline (§10, §16.1b, S13)."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from PIL import Image

from app.services.face_verification_service import (
    check_photo_authenticity,
    check_photo_temporal_diversity,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ══════════════════════════════════════════════════════════════════════
# EXIF checks
# ══════════════════════════════════════════════════════════════════════


def test_exif_check_flags_no_exif(tmp_path):
    """Image PIL sans EXIF → flags contient 'no_exif_data'."""
    img = Image.new("RGB", (800, 600), (128, 128, 128))
    path = str(tmp_path / "no_exif.png")
    img.save(path, format="PNG")

    result = check_photo_authenticity(path)
    assert "no_exif_data" in result["flags"]
    assert result["risk"] > 0


def test_exif_check_flags_square_resolution(tmp_path):
    """Image 1024x1024 → flags contient 'square_suspicious_resolution'."""
    img = Image.new("RGB", (1024, 1024), (200, 200, 200))
    path = str(tmp_path / "square.png")
    img.save(path, format="PNG")

    result = check_photo_authenticity(path)
    assert "square_suspicious_resolution" in result["flags"]


def test_exif_check_passes_normal_photo(tmp_path):
    """Image avec EXIF normal (camera + date) → risk = 0.0."""
    import piexif

    # Create a JPEG with proper EXIF
    img = Image.new("RGB", (800, 600), (100, 150, 200))
    exif_dict = {
        "0th": {
            piexif.ImageIFD.Model: b"Samsung Galaxy S24",
            piexif.ImageIFD.Software: b"Android 14",
        },
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: b"2026:04:15 14:30:00",
        },
    }
    exif_bytes = piexif.dump(exif_dict)
    path = str(tmp_path / "normal.jpg")
    img.save(path, format="JPEG", exif=exif_bytes)

    result = check_photo_authenticity(path)
    assert result["risk"] == 0.0
    assert len(result["flags"]) == 0


# ══════════════════════════════════════════════════════════════════════
# Temporal diversity
# ══════════════════════════════════════════════════════════════════════


def test_temporal_diversity_flags_same_day(tmp_path):
    """4 photos meme date EXIF → risk 0.3 + action flag."""
    import piexif

    photos = []
    for i in range(4):
        img = Image.new("RGB", (200, 300), (i * 50, i * 50, i * 50))
        exif_dict = {
            "Exif": {
                piexif.ExifIFD.DateTimeOriginal: b"2026:04:17 10:00:00",
            },
        }
        exif_bytes = piexif.dump(exif_dict)
        path = str(tmp_path / f"photo_{i}.jpg")
        img.save(path, format="JPEG", exif=exif_bytes)
        mock_photo = MagicMock()
        mock_photo.original_url = f"/uploads/test/{path}"
        photos.append(mock_photo)

    with patch(
        "app.services.face_verification_service.get_photo_disk_path",
        side_effect=[str(tmp_path / f"photo_{i}.jpg") for i in range(4)],
    ):
        result = check_photo_temporal_diversity(photos)

    assert result["status"] == "all_same_day"
    assert result["risk"] == 0.3
    assert result["action"] == "flag_for_review"


def test_temporal_diversity_passes_diverse(tmp_path):
    """Photos dates differentes → risk 0.0."""
    import piexif

    dates = [b"2026:04:10 10:00:00", b"2026:04:12 14:00:00", b"2026:04:15 09:00:00"]
    photos = []
    for i, d in enumerate(dates):
        img = Image.new("RGB", (200, 300), (i * 50, i * 50, i * 50))
        exif_dict = {"Exif": {piexif.ExifIFD.DateTimeOriginal: d}}
        exif_bytes = piexif.dump(exif_dict)
        path = str(tmp_path / f"diverse_{i}.jpg")
        img.save(path, format="JPEG", exif=exif_bytes)
        mock_photo = MagicMock()
        photos.append(mock_photo)

    with patch(
        "app.services.face_verification_service.get_photo_disk_path",
        side_effect=[str(tmp_path / f"diverse_{i}.jpg") for i in range(3)],
    ):
        result = check_photo_temporal_diversity(photos)

    assert result["status"] == "diverse"
    assert result["risk"] == 0.0


# ══════════════════════════════════════════════════════════════════════
# Pipeline complete (mode onnx sans modeles)
# ══════════════════════════════════════════════════════════════════════


async def test_moderate_photo_onnx_exif_only(db_session, redis_client):
    """Mode onnx sans modeles ML → EXIF + temporal seulement, photo approved."""
    from contextlib import asynccontextmanager

    from tests._feed_setup import seed_ama_and_kofi

    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    # Get one of ama's photos
    from sqlalchemy import select
    from app.models.photo import Photo

    result = await db_session.execute(
        select(Photo).where(Photo.user_id == ama.id).limit(1)
    )
    photo = result.scalar_one()
    # Set to pending for moderation
    photo.moderation_status = "pending"
    await db_session.flush()

    # Create a real image at the expected disk path
    import os

    from app.services.photo_service import get_photo_disk_path

    disk_path = get_photo_disk_path(photo)
    os.makedirs(os.path.dirname(disk_path), exist_ok=True)
    img = Image.new("RGB", (800, 600), (128, 128, 128))
    img.save(disk_path, format="WEBP")

    from app.tasks.photo_tasks import _moderate_photo_onnx_async

    # Mock async_session to reuse the test's db_session
    @asynccontextmanager
    async def _fake_session():
        yield db_session

    with patch("app.tasks.photo_tasks.async_session", _fake_session):
        res = await _moderate_photo_onnx_async(str(photo.id))

    assert res["status"] == "approved"
    assert "exif" in res["checks"]
    # ML checks should all be skipped
    assert res["checks"]["nsfw"]["status"] == "skip"
    assert res["checks"]["selfie_compare"]["status"] == "skip"
    assert res["checks"]["gender"]["status"] == "skip"

    # Cleanup
    os.remove(disk_path)


async def test_pipeline_check3_no_face_flags(db_session, redis_client):
    """Pipeline avec YuNet mock retournant 0 faces → risk 0.3."""
    from contextlib import asynccontextmanager

    from tests._feed_setup import seed_ama_and_kofi

    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    from sqlalchemy import select
    from app.models.photo import Photo

    result = await db_session.execute(
        select(Photo).where(Photo.user_id == ama.id).limit(1)
    )
    photo = result.scalar_one()
    photo.moderation_status = "pending"
    await db_session.flush()

    import os
    from app.services.photo_service import get_photo_disk_path

    disk_path = get_photo_disk_path(photo)
    os.makedirs(os.path.dirname(disk_path), exist_ok=True)
    img = Image.new("RGB", (800, 600), (128, 128, 128))
    img.save(disk_path, format="WEBP")

    from app.tasks.photo_tasks import _moderate_photo_onnx_async
    from app.services.face_verification_service import face_service

    @asynccontextmanager
    async def _fake_session():
        yield db_session

    # Mock YuNet to return 0 faces (model loaded but no face found)
    face_service._yunet_loaded = True
    original_detector = face_service._yunet_detector
    mock_det = MagicMock()
    mock_det.detect.return_value = (0, None)
    mock_det.setInputSize = MagicMock()
    face_service._yunet_detector = mock_det

    import sys
    import numpy as _np
    mock_cv2 = MagicMock()
    mock_cv2.imread.return_value = _np.zeros((320, 320, 3), dtype=_np.uint8)
    mock_cv2.resize.return_value = _np.zeros((320, 320, 3), dtype=_np.uint8)
    sys.modules["cv2"] = mock_cv2

    try:
        with patch("app.tasks.photo_tasks.async_session", _fake_session):
            res = await _moderate_photo_onnx_async(str(photo.id))
    finally:
        face_service._yunet_detector = original_detector
        face_service._yunet_loaded = False
        del sys.modules["cv2"]

    assert res["checks"]["face_detection"]["status"] == "no_face"
    assert res["checks"]["face_detection"]["risk"] == 0.3

    os.remove(disk_path)
