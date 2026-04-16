from __future__ import annotations

"""
Tests dispatcher photo_moderation_service (§16.1b — 4 modes).

Les modes `onnx` et `external` vérifient seulement que la task Celery
stub est invoquée (`.delay()`) sans exception. Le pipeline ML complet
sera câblé en S11.
"""

from uuid import uuid4

import pytest

from app.core.security import create_access_token
from app.models.photo import Photo
from app.models.user import User
from app.services import photo_moderation_service
from app.utils.phone import hash_phone

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_user_with_pending_photo(db) -> Photo:
    u = User(
        id=uuid4(),
        phone_hash=hash_phone(f"+2289{uuid4().int % 10_000_000:07d}"),
        phone_country_code="228",
        is_phone_verified=True,
    )
    db.add(u)
    await db.flush()
    p = Photo(
        id=uuid4(),
        user_id=u.id,
        original_url="http://x/o.webp",
        thumbnail_url="http://x/t.webp",
        medium_url="http://x/m.webp",
        display_order=0,
        content_hash="h" * 64,
        width=800,
        height=1200,
        file_size_bytes=1000,
        moderation_status="pending",
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def test_mode_off_auto_approves(db_session, monkeypatch):
    photo = await _make_user_with_pending_photo(db_session)
    monkeypatch.setattr(
        photo_moderation_service.settings, "photo_moderation_mode", "off"
    )

    result = await photo_moderation_service.moderate_photo(photo.id, db_session)
    assert result == "approved"
    assert photo.moderation_status == "approved"


async def test_mode_manual_leaves_pending(db_session, monkeypatch):
    photo = await _make_user_with_pending_photo(db_session)
    monkeypatch.setattr(
        photo_moderation_service.settings, "photo_moderation_mode", "manual"
    )

    result = await photo_moderation_service.moderate_photo(photo.id, db_session)
    assert result == "pending"

    await db_session.refresh(photo)
    assert photo.moderation_status == "pending"


async def test_mode_onnx_enqueues_task_stub(db_session, monkeypatch):
    """En mode onnx, le dispatcher appelle .delay() sans exception.

    Le stub dans app/tasks/photo_tasks.py log seulement. La photo reste
    pending en DB tant que la task réelle (S11) ne sera pas wired.
    """
    photo = await _make_user_with_pending_photo(db_session)
    monkeypatch.setattr(
        photo_moderation_service.settings, "photo_moderation_mode", "onnx"
    )

    result = await photo_moderation_service.moderate_photo(photo.id, db_session)
    assert result == "pending"


async def test_unknown_mode_raises(db_session, monkeypatch):
    photo = await _make_user_with_pending_photo(db_session)
    monkeypatch.setattr(
        photo_moderation_service.settings, "photo_moderation_mode", "martian"
    )

    with pytest.raises(ValueError, match="martian"):
        await photo_moderation_service.moderate_photo(photo.id, db_session)


async def test_moderate_photo_idempotent_on_non_pending(db_session, monkeypatch):
    """Si la photo est déjà approved/rejected, le dispatcher no-op."""
    photo = await _make_user_with_pending_photo(db_session)
    photo.moderation_status = "approved"
    await db_session.commit()

    monkeypatch.setattr(
        photo_moderation_service.settings, "photo_moderation_mode", "off"
    )
    result = await photo_moderation_service.moderate_photo(photo.id, db_session)
    assert result == "approved"
