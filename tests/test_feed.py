from __future__ import annotations

"""
Tests Feed (§5.6).

Couvre : GET /feed (cold + warm), like (happy/mutual/idempotent/quota),
skip (happy/idempotent), view (BehaviorLog + behavior update), câblage
update_behavior_on_action, generate_single_feed batch persist.
"""

import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.constants import (
    MATCHING_FEED_MIN_SIZE,
    MATCHING_FEED_SIZE,
    REDIS_BEHAVIOR_STATS_KEY,
)
from app.models.behavior_log import BehaviorLog
from app.models.feed_cache import FeedCache
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


# ══════════════════════════════════════════════════════════════════════
# GET /feed — cache froid / chaud
# ══════════════════════════════════════════════════════════════════════


async def _seed_pool_for_ama(db) -> dict:
    """Crée Ama + 10 hommes compatibles pour remplir le feed."""
    base = await seed_city_lome(db)
    q = base["quartiers"]
    s = base["spots"]

    ama = await make_user(
        db, phone="+22890001001", city_id=base["city"].id,
        display_name="Ama", gender="woman", seeking="men",
        birth_year=1999, tags=["foodie"],
    )
    await attach_quartier(db, ama, q["tokoin"], "lives")
    await attach_spot(db, ama, s["cafe21"], "regular", 0.8)

    # 10 hommes compatibles
    men = []
    for i in range(10):
        m = await make_user(
            db, phone=f"+22890001{100+i:03d}",
            city_id=base["city"].id,
            display_name=f"M{i}", gender="man", seeking="women",
            birth_year=1994, tags=["foodie"] if i % 2 == 0 else ["sport"],
        )
        await attach_quartier(
            db, m,
            q["tokoin"] if i % 2 == 0 else q["be"],
            "lives",
        )
        await attach_spot(
            db, m,
            s["cafe21"] if i % 2 == 0 else s["tonton"],
            "confirmed", 0.6,
        )
        men.append(m)

    await db.commit()
    await db.refresh(ama)
    for m in men:
        await db.refresh(m)
    return {**base, "ama": ama, "men": men}


async def test_feed_cold_returns_profiles_and_caches(client, db_session, redis_client):
    """GET /feed initial → pipeline exécuté, Redis + FeedCache remplis, 8-12 profils."""
    data = await _seed_pool_for_ama(db_session)
    ama = data["ama"]

    resp = await client.get("/feed", headers=headers_for(ama))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["feed_date"]
    assert MATCHING_FEED_MIN_SIZE <= len(body["profiles"]) <= MATCHING_FEED_SIZE
    # Structure d'un FeedProfileItem
    first = body["profiles"][0]
    for field in (
        "id", "user_id", "display_name", "age", "photos",
        "tags_in_common", "quartiers", "spots_in_common",
        "geo_score_display", "is_verified", "is_wildcard",
    ):
        assert field in first
    assert body["remaining_likes"] == 5  # quota free par défaut
    assert body["is_premium"] is False

    # Redis a bien le cache
    cached = await redis_client.get(f"feed:{ama.id}")
    assert cached is not None
    payload = json.loads(cached)
    assert len(payload["profile_ids"]) >= MATCHING_FEED_MIN_SIZE

    # DB a bien la row FeedCache
    row = await db_session.execute(
        select(FeedCache).where(FeedCache.user_id == ama.id)
    )
    fc = row.scalar_one_or_none()
    assert fc is not None
    assert len(fc.profile_ids) >= MATCHING_FEED_MIN_SIZE


async def test_feed_warm_uses_cache(client, db_session, redis_client):
    """Deux GET /feed successifs → le second est servi depuis Redis."""
    data = await _seed_pool_for_ama(db_session)
    ama = data["ama"]

    resp1 = await client.get("/feed", headers=headers_for(ama))
    assert resp1.status_code == 200
    ids1 = [p["user_id"] for p in resp1.json()["profiles"]]

    resp2 = await client.get("/feed", headers=headers_for(ama))
    assert resp2.status_code == 200
    ids2 = [p["user_id"] for p in resp2.json()["profiles"]]

    # Ordre identique → preuve qu'on n'a pas re-shufflé (pipeline non réexécuté)
    assert ids1 == ids2


# ══════════════════════════════════════════════════════════════════════
# POST /feed/{id}/like
# ══════════════════════════════════════════════════════════════════════


async def test_like_happy_path(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    resp = await client.post(
        f"/feed/{kofi.id}/like",
        json={},
        headers=headers_for(ama),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "liked"
    assert body["match_id"] is None
    assert body["remaining_likes"] == 4  # 5 - 1


async def test_like_creates_mutual_match_with_icebreaker(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    # Ama like Kofi (pending)
    r1 = await client.post(
        f"/feed/{kofi.id}/like", json={}, headers=headers_for(ama)
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "liked"

    # Kofi like Ama → promotion en match
    r2 = await client.post(
        f"/feed/{ama.id}/like", json={}, headers=headers_for(kofi)
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["status"] == "matched"
    assert body["match_id"] is not None
    assert body["ice_breaker"]  # non vide
    # Ice-breaker de haute priorité : spot commun Café 21
    assert "Café 21" in body["ice_breaker"] or "☕" in body["ice_breaker"] or "📍" in body["ice_breaker"]


async def test_like_idempotency_key_prevents_duplicate(
    client, db_session, redis_client
):
    """3 appels rapides avec la même clé → 1 seul like consommé, même response."""
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    key = str(uuid4())
    headers = {**headers_for(ama), "X-Idempotency-Key": key}

    responses = []
    for _ in range(3):
        r = await client.post(f"/feed/{kofi.id}/like", json={}, headers=headers)
        assert r.status_code == 200, r.text
        responses.append(r.json())

    # Toutes identiques
    assert responses[0] == responses[1] == responses[2]
    # 1 seul like consommé
    assert responses[0]["remaining_likes"] == 4
    # 1 seule row Match
    rows = await db_session.execute(
        select(Match).where(Match.user_a_id == ama.id, Match.user_b_id == kofi.id)
    )
    assert len(list(rows.scalars().all())) == 1


async def test_like_quota_exhausted_returns_429(client, db_session, redis_client):
    """Après 5 likes (quota free), le 6e → 429."""
    base = await seed_city_lome(db_session)
    q = base["quartiers"]
    ama = await make_user(
        db_session, phone="+22890002001", city_id=base["city"].id,
        display_name="Ama", gender="woman", seeking="men", birth_year=1999,
    )
    await attach_quartier(db_session, ama, q["tokoin"], "lives")

    # 6 hommes pour avoir 6 cibles distinctes
    men = []
    for i in range(6):
        m = await make_user(
            db_session, phone=f"+22890002{100+i:03d}",
            city_id=base["city"].id, display_name=f"M{i}",
            gender="man", seeking="women", birth_year=1994,
        )
        await attach_quartier(db_session, m, q["tokoin"], "lives")
        men.append(m)

    await db_session.commit()

    headers = headers_for(ama)
    for i in range(5):
        r = await client.post(f"/feed/{men[i].id}/like", json={}, headers=headers)
        assert r.status_code == 200, (i, r.text)

    r6 = await client.post(f"/feed/{men[5].id}/like", json={}, headers=headers)
    assert r6.status_code == 429
    assert r6.json()["detail"] == "daily_likes_exhausted"


async def test_like_updates_behavior_stats(client, db_session, redis_client):
    """Le câblage update_behavior_on_action s'exécute sur like."""
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    r = await client.post(f"/feed/{kofi.id}/like", json={}, headers=headers_for(ama))
    assert r.status_code == 200

    stats_key = REDIS_BEHAVIOR_STATS_KEY.format(user_id=str(ama.id))
    stats = await redis_client.hgetall(stats_key)
    assert int(stats.get("total_likes", 0)) == 1
    assert int(stats.get("profiles_viewed", 0)) == 1


# ══════════════════════════════════════════════════════════════════════
# POST /feed/{id}/skip
# ══════════════════════════════════════════════════════════════════════


async def test_skip_happy_path(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    resp = await client.post(
        f"/feed/{kofi.id}/skip",
        json={"reason": "too_far"},
        headers=headers_for(ama),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "skipped"

    # Match row status=skipped existe
    row = await db_session.execute(
        select(Match).where(Match.user_a_id == ama.id, Match.user_b_id == kofi.id)
    )
    m = row.scalar_one()
    assert m.status == "skipped"

    # BehaviorLog créé
    row2 = await db_session.execute(
        select(BehaviorLog).where(
            BehaviorLog.user_id == ama.id,
            BehaviorLog.event_type == "skip",
        )
    )
    log = row2.scalar_one()
    assert log.target_user_id == kofi.id
    assert log.extra_data == {"reason": "too_far"}


async def test_skip_idempotency(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    key = str(uuid4())
    headers = {**headers_for(ama), "X-Idempotency-Key": key}

    responses = []
    for _ in range(3):
        r = await client.post(f"/feed/{kofi.id}/skip", json={}, headers=headers)
        assert r.status_code == 200
        responses.append(r.json())

    assert responses[0] == responses[1] == responses[2]

    # 1 seule row Match
    rows = await db_session.execute(
        select(Match).where(Match.user_a_id == ama.id, Match.user_b_id == kofi.id)
    )
    assert len(list(rows.scalars().all())) == 1


# ══════════════════════════════════════════════════════════════════════
# POST /feed/{id}/view
# ══════════════════════════════════════════════════════════════════════


async def test_view_creates_behavior_log_and_updates_stats(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    resp = await client.post(
        f"/feed/{kofi.id}/view",
        json={"duration_seconds": 12.5, "scrolled_full": True, "prompts_viewed": 2},
        headers=headers_for(ama),
    )
    assert resp.status_code == 204

    row = await db_session.execute(
        select(BehaviorLog).where(
            BehaviorLog.user_id == ama.id,
            BehaviorLog.event_type == "view",
            BehaviorLog.target_user_id == kofi.id,
        )
    )
    log = row.scalar_one()
    assert log.duration_seconds == 12.5
    assert log.extra_data["scrolled_full"] is True
    assert log.extra_data["prompts_viewed"] == 2

    # update_behavior_on_action câblé
    stats_key = REDIS_BEHAVIOR_STATS_KEY.format(user_id=str(ama.id))
    stats = await redis_client.hgetall(stats_key)
    assert int(stats.get("profiles_viewed", 0)) == 1


# ══════════════════════════════════════════════════════════════════════
# Batch task (Étape 6)
# ══════════════════════════════════════════════════════════════════════


async def test_generate_single_feed_persists(db_session, redis_client):
    """Appel direct de generate_single_feed → Redis + FeedCache remplis."""
    from app.tasks.matching_tasks import generate_single_feed

    data = await _seed_pool_for_ama(db_session)
    ama = data["ama"]

    result = await generate_single_feed(ama.id, db_session, redis_client)
    assert MATCHING_FEED_MIN_SIZE <= len(result["profile_ids"]) <= MATCHING_FEED_SIZE

    # Redis
    cached = await redis_client.get(f"feed:{ama.id}")
    assert cached is not None
    payload = json.loads(cached)
    assert len(payload["profile_ids"]) == len(result["profile_ids"])

    # DB
    row = await db_session.execute(
        select(FeedCache).where(FeedCache.user_id == ama.id)
    )
    fc = row.scalar_one()
    assert len(fc.profile_ids) == len(result["profile_ids"])


async def test_generate_all_feeds_respects_timezone_window(
    db_session, redis_client
):
    """
    Le bucketing timezone skip les villes hors fenêtre locale [3h, 5h[.
    À 12h UTC, Lomé (UTC+0) est à 12h local → doit être skip si
    trigger_utc_hour fourni.
    """
    from datetime import datetime, timezone as _tz
    from unittest.mock import patch

    from app.tasks.matching_tasks import generate_all_feeds

    data = await _seed_pool_for_ama(db_session)

    # Freeze now à 12h UTC — Lomé local = 12h → hors [3,5[
    fake_now = datetime(2026, 4, 16, 12, 0, tzinfo=_tz.utc)
    with patch("app.tasks.matching_tasks.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = await generate_all_feeds(
            db_session, redis_client, trigger_utc_hour=12
        )

    assert data["city"].id in result["cities_skipped"]
    assert data["city"].id not in result["cities_processed"]
