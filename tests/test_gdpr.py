from __future__ import annotations

"""
Tests pipeline RGPD Phase 1 (§17) :
  - gdpr_service.apply_phase1_db_changes : anonymize, photos.is_deleted,
    clôture matches actifs
  - purge_user_redis_keys : Redis feed/behavior
  - DELETE /auth/account : orchestration complète (handler)
"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.constants import (
    REDIS_BEHAVIOR_KEY,
    REDIS_BEHAVIOR_STATS_KEY,
    REDIS_IMPLICIT_PREFS_KEY,
)
from app.core.security import create_access_token
from app.models.match import Match
from app.models.photo import Photo
from app.models.profile import Profile
from app.models.user import User
from app.services import gdpr_service
from app.utils.phone import hash_phone

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_user_with_profile(db, display="Kofi", gender="man") -> User:
    u = User(
        id=uuid4(),
        phone_hash=hash_phone(f"+2289{uuid4().int % 10_000_000:07d}"),
        phone_country_code="228",
        is_phone_verified=True,
    )
    db.add(u)
    await db.flush()
    db.add(
        Profile(
            user_id=u.id,
            display_name=display,
            birth_date=date(1995, 1, 1),
            gender=gender,
            seeking_gender="women" if gender == "man" else "men",
            intention="serious",
            sector="tech",
            prompts=[{"question": "q", "answer": "a"}],
            tags=["foodie"],
            languages=["fr"],
        )
    )
    await db.commit()
    await db.refresh(u)
    return u


async def test_apply_phase1_anonymizes_profile(db_session):
    u = await _make_user_with_profile(db_session)
    assert u.profile.display_name == "Kofi"

    result = await gdpr_service.apply_phase1_db_changes(u, db_session)
    await db_session.commit()

    assert result["photos_marked"] == 0
    assert result["matches_closed"] == 0

    await db_session.refresh(u.profile)
    assert u.profile.display_name == gdpr_service.ANONYMIZED_DISPLAY_NAME
    assert u.profile.prompts == []
    assert u.profile.tags == []
    assert u.profile.languages == []


async def test_apply_phase1_marks_photos_soft_deleted(db_session):
    u = await _make_user_with_profile(db_session)
    photo = Photo(
        id=uuid4(),
        user_id=u.id,
        original_url="http://x/a.webp",
        thumbnail_url="http://x/a_t.webp",
        medium_url="http://x/a_m.webp",
        display_order=0,
        content_hash="h" * 64,
        width=800,
        height=1200,
        file_size_bytes=1000,
        moderation_status="approved",
    )
    db_session.add(photo)
    await db_session.commit()

    result = await gdpr_service.apply_phase1_db_changes(u, db_session)
    await db_session.commit()

    assert result["photos_marked"] == 1
    await db_session.refresh(photo)
    assert photo.is_deleted is True
    assert photo.moderation_status == "deleted"


async def test_apply_phase1_closes_active_matches(db_session):
    u1 = await _make_user_with_profile(db_session, "A", "woman")
    u2 = await _make_user_with_profile(db_session, "B", "man")
    match = Match(
        id=uuid4(),
        user_a_id=u1.id,
        user_b_id=u2.id,
        status="matched",
        matched_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db_session.add(match)
    await db_session.commit()

    result = await gdpr_service.apply_phase1_db_changes(u1, db_session)
    await db_session.commit()

    assert result["matches_closed"] == 1
    await db_session.refresh(match)
    assert match.status == "expired"
    assert match.unmatched_by == u1.id
    assert match.unmatched_at is not None


async def test_purge_user_redis_keys_deletes_all(db_session, redis_client):
    uid = uuid4()
    keys = [
        f"feed:{uid}",
        REDIS_BEHAVIOR_KEY.format(user_id=uid),
        REDIS_BEHAVIOR_STATS_KEY.format(user_id=uid),
        REDIS_IMPLICIT_PREFS_KEY.format(user_id=uid),
    ]
    for k in keys:
        await redis_client.set(k, "sentinel")

    await gdpr_service.purge_user_redis_keys(uid, redis_client)

    for k in keys:
        assert await redis_client.get(k) is None


async def test_delete_account_endpoint_triggers_phase1(
    client, db_session, redis_client
):
    u = await _make_user_with_profile(db_session, "ToDelete", "woman")
    photo = Photo(
        id=uuid4(),
        user_id=u.id,
        original_url="http://x/a.webp",
        thumbnail_url="http://x/a_t.webp",
        medium_url="http://x/a_m.webp",
        display_order=0,
        content_hash="h" * 64,
        width=800,
        height=1200,
        file_size_bytes=1000,
        moderation_status="approved",
    )
    db_session.add(photo)
    await db_session.commit()

    headers = {"Authorization": f"Bearer {create_access_token(u.id)}"}
    resp = await client.request(
        "DELETE",
        "/auth/account",
        headers=headers,
        json={"confirm": True, "reason": "user_deleted"},
    )
    assert resp.status_code == 204, resp.text

    await db_session.refresh(u)
    assert u.is_deleted is True
    assert u.deleted_at is not None
    assert u.is_active is False
    assert u.is_visible is False

    await db_session.refresh(u.profile)
    assert u.profile.display_name == gdpr_service.ANONYMIZED_DISPLAY_NAME

    await db_session.refresh(photo)
    assert photo.is_deleted is True
