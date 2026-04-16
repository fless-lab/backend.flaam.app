from __future__ import annotations

"""
L4 — Multiplicateur comportemental (spec §6.3c).

Pas un "score" mais un MULTIPLICATEUR ∈ [min, max] appliqué au score final.
Récompense un usage sain de l'app ; pénalise le spam et le ghosting.

4 composantes, chacune produit un facteur via interpolation linéaire :
  1. response_quality  — matches auxquels l'utilisateur répond
  2. selectivity       — ratio likes / profils vus (sweet spot 15-45%)
  3. richness          — completeness du profil
  4. depth             — messages moyens par match

Multiplier final = produit borné à [behavior_min_multiplier, behavior_max_multiplier].

Signature lecture :
    Input  : candidate_ids, redis_client, db_session
    Output : dict[UUID, float]

Signature écriture (side effect) :
    update_behavior_on_action(user_id, action_type, metadata, redis, db, config)
        → recalcule + écrit Redis "behavior:{user_id}" + Profile.behavior_multiplier
"""

from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    REDIS_BEHAVIOR_KEY,
    REDIS_BEHAVIOR_STATS_KEY,
)
from app.models.profile import Profile


def _lerp(min_val: float, max_val: float, t: float) -> float:
    """Interpolation linéaire bornée."""
    t = max(0.0, min(1.0, t))
    return min_val + (max_val - min_val) * t


# ── Lecture ──


async def get_behavior_multipliers(
    candidate_ids: list[UUID],
    redis_client: aioredis.Redis,
    db_session: AsyncSession,
) -> dict[UUID, float]:
    """
    Retourne {candidate_id: multiplier}. Redis d'abord, fallback DB
    (`Profile.behavior_multiplier`, defaut 1.0).
    """
    if not candidate_ids:
        return {}

    # Batch Redis MGET
    keys = [REDIS_BEHAVIOR_KEY.format(user_id=str(cid)) for cid in candidate_ids]
    cached_values = await redis_client.mget(keys)

    multipliers: dict[UUID, float] = {}
    missing: list[UUID] = []
    for cid, v in zip(candidate_ids, cached_values):
        if v is None:
            missing.append(cid)
            continue
        try:
            multipliers[cid] = float(v)
        except (TypeError, ValueError):
            missing.append(cid)

    if missing:
        rows = await db_session.execute(
            select(Profile.user_id, Profile.behavior_multiplier).where(
                Profile.user_id.in_(missing)
            )
        )
        db_map = {uid: float(m or 1.0) for uid, m in rows.all()}
        for cid in missing:
            multipliers[cid] = db_map.get(cid, 1.0)

    return multipliers


# ── Écriture ──


async def update_behavior_on_action(
    user_id: UUID,
    action_type: str,
    metadata: dict | None,
    redis_client: aioredis.Redis,
    db_session: AsyncSession,
    config: dict[str, float],
) -> float:
    """
    Met à jour les compteurs et recalcule le multiplicateur.

    Actions supportées :
      - "like"              : +1 total_likes, +1 profiles_viewed
      - "skip"              : +1 total_skips, +1 profiles_viewed
      - "profile_viewed"    : +1 profiles_viewed
      - "message_sent"      : +1 messages_sent
      - "message_received"  : +1 messages_received
      - "match_created"     : +1 total_matches
      - "match_response"    : +1 matches_responded

    Retourne le nouveau multiplicateur.
    """
    stats_key = REDIS_BEHAVIOR_STATS_KEY.format(user_id=str(user_id))

    # 1. Incrémente d'abord les compteurs impactés par l'action.
    pipe = redis_client.pipeline()
    if action_type == "like":
        pipe.hincrby(stats_key, "total_likes", 1)
        pipe.hincrby(stats_key, "profiles_viewed", 1)
    elif action_type == "skip":
        pipe.hincrby(stats_key, "total_skips", 1)
        pipe.hincrby(stats_key, "profiles_viewed", 1)
    elif action_type == "profile_viewed":
        pipe.hincrby(stats_key, "profiles_viewed", 1)
    elif action_type == "message_sent":
        pipe.hincrby(stats_key, "messages_sent", 1)
    elif action_type == "message_received":
        pipe.hincrby(stats_key, "messages_received", 1)
    elif action_type == "match_created":
        pipe.hincrby(stats_key, "total_matches", 1)
    elif action_type == "match_response":
        pipe.hincrby(stats_key, "matches_responded", 1)
    await pipe.execute()

    # 2. Relis les stats courantes
    stats = await redis_client.hgetall(stats_key)

    def _n(k: str) -> int:
        try:
            return int(stats.get(k, 0) or 0)
        except (TypeError, ValueError):
            return 0

    total_likes = _n("total_likes")
    total_profiles = _n("profiles_viewed")
    messages_sent = _n("messages_sent")
    total_matches = _n("total_matches")
    matches_responded = _n("matches_responded")

    # 3. response_quality
    if total_matches > 0:
        response_rate = matches_responded / total_matches
        response_factor = _lerp(
            config.get("behavior_response_min", 0.6),
            config.get("behavior_response_max", 1.4),
            min(1.0, response_rate),
        )
    else:
        response_factor = 1.0

    # 4. selectivity (≥ 10 profils vus pour évaluer)
    if total_profiles > 10:
        like_rate = total_likes / total_profiles
        if 0.15 <= like_rate <= 0.45:
            selectivity_factor = config.get("behavior_selectivity_max", 1.3)
        elif like_rate > 0.80 or like_rate < 0.05:
            selectivity_factor = config.get("behavior_selectivity_min", 0.7)
        else:
            selectivity_factor = 1.0
    else:
        selectivity_factor = 1.0

    # 5. richness — depuis le profil DB
    profile_row = await db_session.execute(
        select(Profile).where(Profile.user_id == user_id)
    )
    profile = profile_row.scalar_one_or_none()
    completeness = float(profile.profile_completeness) if profile else 0.0
    richness_factor = _lerp(
        config.get("behavior_richness_min", 0.8),
        config.get("behavior_richness_max", 1.2),
        completeness,
    )

    # 6. depth
    if messages_sent > 0 and total_matches > 0:
        avg_msg = messages_sent / max(1, total_matches)
        depth_factor = _lerp(
            config.get("behavior_depth_min", 0.8),
            config.get("behavior_depth_max", 1.3),
            min(1.0, avg_msg / 5.0),
        )
    else:
        depth_factor = 1.0

    # 7. Multiplicateur final borné
    raw = response_factor * selectivity_factor * richness_factor * depth_factor
    lo = config.get("behavior_min_multiplier", 0.6)
    hi = config.get("behavior_max_multiplier", 1.4)
    multiplier = max(lo, min(hi, raw))

    # 8. Écrit Redis + profil
    await redis_client.set(
        REDIS_BEHAVIOR_KEY.format(user_id=str(user_id)), f"{multiplier:.3f}"
    )
    if profile is not None:
        profile.behavior_multiplier = multiplier
        await db_session.flush()

    return multiplier


__all__ = ["get_behavior_multipliers", "update_behavior_on_action"]
