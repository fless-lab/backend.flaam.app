from __future__ import annotations

"""
Tests Matches (§5.7).

Couvre : list_matches, match_detail, unmatch, likes-received (free/premium).
"""

import pytest
from sqlalchemy import select

from app.models.match import Match
from app.services.matching_engine import geo_scorer
from tests._feed_setup import (
    attach_quartier,
    attach_spot,
    headers_for,
    make_user,
    seed_ama_and_kofi,
    seed_city_lome,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture(autouse=True)
def _reset_geo_cache():
    geo_scorer.reset_proximity_cache()
    yield
    geo_scorer.reset_proximity_cache()


async def _create_mutual_match(client, db_session, ama, kofi) -> str:
    """Ama like Kofi, puis Kofi like Ama → retourne le match_id."""
    await client.post(f"/feed/{kofi.id}/like", json={}, headers=headers_for(ama))
    r = await client.post(f"/feed/{ama.id}/like", json={}, headers=headers_for(kofi))
    assert r.status_code == 200
    match_id = r.json()["match_id"]
    assert match_id
    return match_id


# ══════════════════════════════════════════════════════════════════════
# GET /matches
# ══════════════════════════════════════════════════════════════════════


async def test_list_matches_after_mutual(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    match_id = await _create_mutual_match(client, db_session, ama, kofi)

    resp = await client.get("/matches", headers=headers_for(ama))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["matches"]) == 1
    first = body["matches"][0]
    assert first["match_id"] == match_id
    assert first["user"]["display_name"] == "Kofi"
    assert first["unread_count"] == 0


async def test_list_matches_empty(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    resp = await client.get("/matches", headers=headers_for(data["ama"]))
    assert resp.status_code == 200
    assert resp.json()["matches"] == []


# ══════════════════════════════════════════════════════════════════════
# GET /matches/{id}
# ══════════════════════════════════════════════════════════════════════


async def test_match_detail_returns_icebreaker(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    match_id = await _create_mutual_match(client, db_session, ama, kofi)

    resp = await client.get(f"/matches/{match_id}", headers=headers_for(ama))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["match_id"] == match_id
    assert body["status"] == "matched"
    assert body["ice_breaker"]  # non vide
    assert body["user"]["display_name"] == "Kofi"


async def test_match_detail_404_if_not_mine(client, db_session, redis_client):
    from uuid import uuid4

    data = await seed_ama_and_kofi(db_session)
    resp = await client.get(
        f"/matches/{uuid4()}", headers=headers_for(data["ama"])
    )
    assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════
# DELETE /matches/{id}
# ══════════════════════════════════════════════════════════════════════


async def test_unmatch_sets_status_unmatched(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    match_id = await _create_mutual_match(client, db_session, ama, kofi)

    resp = await client.delete(f"/matches/{match_id}", headers=headers_for(ama))
    assert resp.status_code == 200
    assert resp.json()["status"] == "unmatched"

    # En DB
    db_session.expire_all()
    row = await db_session.execute(select(Match).where(Match.id == match_id))
    m = row.scalar_one()
    assert m.status == "unmatched"
    assert m.unmatched_by == ama.id

    # N'apparaît plus dans /matches
    resp2 = await client.get("/matches", headers=headers_for(ama))
    assert resp2.json()["matches"] == []


# ══════════════════════════════════════════════════════════════════════
# GET /matches/likes-received
# ══════════════════════════════════════════════════════════════════════


async def test_likes_received_403_for_free_user(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    resp = await client.get(
        "/matches/likes-received", headers=headers_for(data["ama"])
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "premium_required"


async def test_likes_received_premium_filters_matched_and_skipped(
    client, db_session, redis_client
):
    """
    Setup : Ama (premium) a 3 likers. Elle a déjà skippé 1, matché 1.
    /likes-received ne doit retourner que le 3e.
    """
    base = await seed_city_lome(db_session)
    q = base["quartiers"]

    ama = await make_user(
        db_session, phone="+22890003001", city_id=base["city"].id,
        display_name="Ama", gender="woman", seeking="men",
        birth_year=1999, is_premium=True,
    )
    await attach_quartier(db_session, ama, q["tokoin"], "lives")

    likers = []
    for i in range(3):
        m = await make_user(
            db_session, phone=f"+22890003{100+i:03d}",
            city_id=base["city"].id,
            display_name=f"L{i}", gender="man", seeking="women",
            birth_year=1994,
        )
        await attach_quartier(db_session, m, q["tokoin"], "lives")
        likers.append(m)
    await db_session.commit()

    # Chaque liker like Ama
    for m in likers:
        r = await client.post(
            f"/feed/{ama.id}/like", json={}, headers=headers_for(m)
        )
        assert r.status_code == 200

    # Ama skip L0
    await client.post(
        f"/feed/{likers[0].id}/skip", json={}, headers=headers_for(ama)
    )
    # Ama like L1 → match mutuel
    await client.post(
        f"/feed/{likers[1].id}/like", json={}, headers=headers_for(ama)
    )
    # L2 reste un like non-répondu

    resp = await client.get(
        "/matches/likes-received", headers=headers_for(ama)
    )
    assert resp.status_code == 200, resp.text
    profiles = resp.json()["profiles"]
    ids = [p["user_id"] for p in profiles]
    assert str(likers[2].id) in ids
    assert str(likers[0].id) not in ids  # skippé
    assert str(likers[1].id) not in ids  # déjà matché
