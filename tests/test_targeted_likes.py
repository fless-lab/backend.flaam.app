from __future__ import annotations

"""Tests Feature A — targeted likes (Session 9)."""

import pytest
from sqlalchemy import select

from app.models.match import Match
from app.models.matching_config import MatchingConfig
from app.models.profile import Profile
from tests._feed_setup import headers_for, seed_ama_and_kofi

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _enable_targeted_likes(db):
    db.add(
        MatchingConfig(
            key="flag_targeted_likes_enabled",
            value=1.0,
            category="flags",
        )
    )
    await db.commit()


async def test_like_with_target_photo_when_flag_active(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    await _enable_targeted_likes(db_session)

    r = await client.post(
        f"/feed/{kofi.id}/like",
        json={
            "target_type": "photo",
            "target_id": "photo-123",
            "comment": "Cette photo au café est top",
        },
        headers=headers_for(ama),
    )
    assert r.status_code == 200, r.text

    # Le Match doit porter le targeting
    row = await db_session.execute(
        select(Match).where(
            Match.user_a_id == ama.id, Match.user_b_id == kofi.id
        )
    )
    match = row.scalar_one()
    assert match.like_target_type == "photo"
    assert match.like_target_id == "photo-123"
    assert match.like_comment == "Cette photo au café est top"


async def test_like_with_target_ignored_when_flag_disabled(
    client, db_session, redis_client
):
    """Flag désactivé (default 0.0) → champs target ignorés silencieusement."""
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    r = await client.post(
        f"/feed/{kofi.id}/like",
        json={
            "target_type": "photo",
            "target_id": "photo-456",
            "comment": "ignored",
        },
        headers=headers_for(ama),
    )
    assert r.status_code == 200

    row = await db_session.execute(
        select(Match).where(
            Match.user_a_id == ama.id, Match.user_b_id == kofi.id
        )
    )
    match = row.scalar_one()
    assert match.like_target_type is None
    assert match.like_target_id is None
    assert match.like_comment is None


async def test_comment_becomes_ice_breaker_on_mutual_match(
    client, db_session, redis_client
):
    """
    Si un like contient un comment et le recipient like en retour,
    le comment est servi comme ice_breaker.
    """
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    await _enable_targeted_likes(db_session)

    # Ama like Kofi avec un comment
    r1 = await client.post(
        f"/feed/{kofi.id}/like",
        json={
            "target_type": "prompt",
            "target_id": "maquis",
            "comment": "Le poulet braisé, j'adore aussi !",
        },
        headers=headers_for(ama),
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "liked"

    # Kofi like Ama en retour → match mutuel
    r2 = await client.post(
        f"/feed/{ama.id}/like",
        json={},
        headers=headers_for(kofi),
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "matched"
    assert body["ice_breaker"] == "Le poulet braisé, j'adore aussi !"


async def test_like_on_prompt_increments_like_count(
    client, db_session, redis_client
):
    """
    Feature B — tracking A/B passif : target_type=prompt + target_id
    incrémente prompts[*].like_count dans le JSONB du target.
    """
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    await _enable_targeted_likes(db_session)

    # Kofi a un prompt "maquis" (voir _feed_setup.seed_ama_and_kofi)
    r = await client.post(
        f"/feed/{kofi.id}/like",
        json={"target_type": "prompt", "target_id": "maquis"},
        headers=headers_for(ama),
    )
    assert r.status_code == 200

    prof_row = await db_session.execute(
        select(Profile).where(Profile.user_id == kofi.id)
    )
    profile = prof_row.scalar_one()
    await db_session.refresh(profile, ["prompts"])
    # Trouve l'entrée "maquis" et vérifie like_count
    matching = [
        p for p in (profile.prompts or [])
        if isinstance(p, dict) and p.get("prompt_id") == "maquis"
    ]
    assert matching, "prompt maquis absent"
    assert matching[0].get("like_count") == 1
