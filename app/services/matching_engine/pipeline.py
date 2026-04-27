from __future__ import annotations

"""
Pipeline orchestrateur du matching engine (spec §6.1).

Orchestration L0 → L5 + MàJ 6/7 :

  L0  Charger l'utilisateur (profile + user_quartiers + user_spots)
  L1  Hard filters             → list[UUID]
  L2  Geo scores               → dict[UUID, float 0-1]
  L3  Lifestyle scores         → dict[UUID, float 0-1]
   │   + ajustement implicite ±15% (MàJ 6)
  L4  Behavior multipliers     → dict[UUID, float 0.6-1.4]
  ⊕   Combinaison pondérée (weights adaptatifs selon ancienneté)
  L5  Corrections              → wildcards + new_user_boost + visibility + shuffle
  MàJ 7 First-impression       → re-tri femmes nouvelles (3 premiers feeds)

Retourne 8-12 profile_ids triés pour l'affichage.

TODO — branchements à faire par les sessions suivantes :
  S6 (Matches)    : appeler update_behavior_on_action("like"/"skip"/
                    "match_created"/"match_response") dans les handlers
                    POST /feed/{id}/like, POST /feed/{id}/skip.
  S7 (Chat)       : idem pour "message_sent"/"message_received" dans le
                    handler POST /messages et le WebSocket de chat.
  S9 (Behavior)   : le endpoint POST /behavior/log persiste les
                    BehaviorLog (consommés par compute_implicit_profile).
"""

from datetime import datetime, timezone
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import (
    IMPLICIT_PREFERENCES_ENABLED,
    MATCHING_FEED_LIMIT_ENABLED,
    MATCHING_FEED_MIN_SCORE,
    MATCHING_FEED_MIN_SIZE,
    MATCHING_FEED_SIZE,
)
from app.models.profile import Profile
from app.models.user import User
from app.services.config_service import get_configs
from app.services.matching_engine.age_fit import compute_age_fit
from app.services.matching_engine.behavior_scorer import get_behavior_multipliers
from app.services.matching_engine.corrections import (
    apply_new_user_boost,
    ensure_minimum_visibility,
    inject_wildcards,
    shuffle_feed,
)
from app.services.matching_engine.event_boost import compute_event_boosts
from app.services.matching_engine.first_impression import apply_first_impression
from app.services.matching_engine.geo_scorer import (
    compute_geo_scores,
    load_proximity_cache,
)
from app.services.matching_engine.hard_filters import apply_hard_filters
from app.services.matching_engine.implicit_preferences import (
    apply_implicit_adjustment,
    compute_implicit_profile,
)
from app.services.matching_engine.lifestyle_scorer import compute_lifestyle_scores
from app.services.matching_engine.weights import get_adaptive_weights


# Toutes les clés de config du scorer, chargées d'un bloc au démarrage
# pour éviter N aller-retours Redis dans la boucle.
_CONFIG_KEYS = (
    # L2
    "geo_w_quartier_lives",
    "geo_w_quartier_works",
    "geo_w_quartier_interested",
    "geo_proximity_threshold",
    "geo_unified_set_enabled",
    "geo_unified_bonus_lives",
    "geo_unified_bonus_works",
    "geo_w_quartier",
    "geo_w_spot",
    "geo_w_fidelity",
    "geo_w_freshness",
    "freshness_decay_halflife_days",
    # L3
    "lifestyle_w_tags",
    "lifestyle_w_intention",
    # L5
    "wildcard_count",
    "new_user_boost_count",
    # First impression
    "first_impression_active_feeds",
    "first_impression_min_completeness",
    "first_impression_min_behavior",
    "first_impression_min_photos",
)


async def _load_user_full(
    user_id: UUID, db_session: AsyncSession
) -> User | None:
    stmt = (
        select(User)
        .options(
            selectinload(User.profile),
            selectinload(User.user_quartiers),
            selectinload(User.user_spots),
        )
        .where(User.id == user_id)
    )
    return (await db_session.execute(stmt)).scalar_one_or_none()


async def generate_feed_for_user(
    user_id: UUID,
    db_session: AsyncSession,
    redis_client: aioredis.Redis,
) -> dict:
    """
    Pipeline complet. Retourne :
        {
            "profile_ids": list[UUID],   # 8-12 items (vide si pool insuffisant)
            "wildcards":   list[UUID],
            "new_users":   list[UUID],
        }
    """
    # ── L0 ────────────────────────────────────────────────────────────
    user = await _load_user_full(user_id, db_session)
    if user is None or user.profile is None:
        return {"profile_ids": [], "wildcards": [], "new_users": []}
    if (
        not user.is_active
        or not user.is_visible
        or user.is_banned
        or user.is_deleted
    ):
        return {"profile_ids": [], "wildcards": [], "new_users": []}

    created_at = user.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    account_age_days = (datetime.now(timezone.utc) - created_at).days

    # Bundle config (1 aller-retour Redis)
    config = await get_configs(_CONFIG_KEYS, redis_client, db_session)

    # ── L1 ────────────────────────────────────────────────────────────
    candidate_ids = await apply_hard_filters(user, db_session)
    if not candidate_ids:
        return {"profile_ids": [], "wildcards": [], "new_users": []}

    # ── L2 ────────────────────────────────────────────────────────────
    await load_proximity_cache(user.city_id, db_session)
    geo_scores = await compute_geo_scores(
        user, candidate_ids, config, db_session
    )

    # ── Event boost (MàJ 8 Porte 3 §5) ────────────────────────────────
    # Ajout post-L2 : +0..15 points (sur échelle 0-100) pour les
    # candidats qui étaient au même event récent que l'utilisateur.
    # Les scores géo sont exprimés en [0, 1] ici, donc le boost est
    # divisé par 100 et clampé à 1.0.
    event_boosts = await compute_event_boosts(
        user.id, candidate_ids, db_session
    )
    if event_boosts:
        for cid, bonus_pts in event_boosts.items():
            base = geo_scores.get(cid, 0.0)
            geo_scores[cid] = min(1.0, base + (bonus_pts / 100.0))

    # ── L3 + ajustement implicite ─────────────────────────────────────
    lifestyle_scores = await compute_lifestyle_scores(
        user, candidate_ids, config, db_session
    )
    # Charge les profils candidats une fois — utilisé par implicit + age_fit
    prof_rows = await db_session.execute(
        select(Profile).where(Profile.user_id.in_(candidate_ids))
    )
    cand_profiles = {p.user_id: p for p in prof_rows.scalars()}
    if IMPLICIT_PREFERENCES_ENABLED:
        implicit_profile = await compute_implicit_profile(
            user.id, db_session, redis_client
        )
        if implicit_profile.get("confidence", 0.0) >= 0.3:
            for cid, score in list(lifestyle_scores.items()):
                cp = cand_profiles.get(cid)
                if cp is None:
                    continue
                lifestyle_scores[cid] = apply_implicit_adjustment(
                    score, cp, implicit_profile
                )

    # ── L4 ────────────────────────────────────────────────────────────
    behavior_mults = await get_behavior_multipliers(
        candidate_ids, redis_client, db_session
    )

    # ── Combinaison pondérée ──────────────────────────────────────────
    geo_w, life_w, beh_w = await get_adaptive_weights(
        account_age_days, redis_client, db_session
    )

    # Âge du user (pour age_fit bidirectionnel)
    today = datetime.now(timezone.utc).date()
    user_age = today.year - user.profile.birth_date.year - (
        (today.month, today.day)
        < (user.profile.birth_date.month, user.profile.birth_date.day)
    )

    final_scores: dict[UUID, float] = {}
    for cid in candidate_ids:
        g = geo_scores.get(cid, 0.0)
        lif = lifestyle_scores.get(cid, 0.0)
        m = behavior_mults.get(cid, 1.0)
        # geo + lifestyle pondérés, ajustés par le multiplicateur behavior.
        # Le poids behavior_w ∈ [0.10, 0.45] pilote l'amplitude de l'effet :
        # on blend linéairement entre 1.0 (pas d'effet) et m (effet total).
        behavior_effect = 1.0 + (m - 1.0) * beh_w

        # Age fit : multiplicateur soft ∈ [0.4, 1.0] selon distance hors-range
        age_fit = 1.0
        cp = cand_profiles.get(cid)
        if cp is not None:
            cand_age = today.year - cp.birth_date.year - (
                (today.month, today.day)
                < (cp.birth_date.month, cp.birth_date.day)
            )
            age_fit = compute_age_fit(
                user_age=user_age,
                candidate_age=cand_age,
                user_seeking_age_min=user.profile.seeking_age_min,
                user_seeking_age_max=user.profile.seeking_age_max,
                candidate_seeking_age_min=cp.seeking_age_min,
                candidate_seeking_age_max=cp.seeking_age_max,
            )

        final_scores[cid] = (
            (geo_w * g + life_w * lif) * behavior_effect * age_fit
        )

    sorted_candidates = sorted(
        final_scores.items(), key=lambda x: x[1], reverse=True
    )

    # ── L5 ────────────────────────────────────────────────────────────
    wildcard_count = int(config.get("wildcard_count", 2))
    new_user_count = int(config.get("new_user_boost_count", 2))
    # Filter by minimum score
    sorted_candidates = [
        (cid, score) for cid, score in sorted_candidates
        if score >= MATCHING_FEED_MIN_SCORE
    ]

    if MATCHING_FEED_LIMIT_ENABLED:
        top_n = max(
            MATCHING_FEED_MIN_SIZE,
            MATCHING_FEED_SIZE - wildcard_count - new_user_count,
        )
        top_profiles = [cid for cid, _ in sorted_candidates[:top_n]]
    else:
        # No limit — return all profiles above min score
        top_profiles = [cid for cid, _ in sorted_candidates]

    wildcards = await inject_wildcards(
        user=user,
        top_profiles=top_profiles,
        sorted_candidates=sorted_candidates,
        geo_scores=geo_scores,
        lifestyle_scores=lifestyle_scores,
        count=wildcard_count,
        db_session=db_session,
    )

    already_in = set(top_profiles) | set(wildcards)
    remaining = [cid for cid, _ in sorted_candidates if cid not in already_in]
    new_users = await apply_new_user_boost(
        remaining, new_user_count, db_session
    )

    feed_ids = top_profiles + wildcards + new_users

    # Sécurité : si le pool total dépasse la taille max, on tronque.
    # Si on a moins que MATCHING_FEED_MIN_SIZE, on complète avec les suivants.
    if len(feed_ids) < MATCHING_FEED_MIN_SIZE:
        used = set(feed_ids)
        for cid, _ in sorted_candidates:
            if cid in used:
                continue
            feed_ids.append(cid)
            used.add(cid)
            if MATCHING_FEED_LIMIT_ENABLED and len(feed_ids) >= MATCHING_FEED_SIZE:
                break
    if MATCHING_FEED_LIMIT_ENABLED:
        feed_ids = feed_ids[:MATCHING_FEED_SIZE]

    feed_ids = await ensure_minimum_visibility(
        feed_ids, user, redis_client, db_session
    )

    # ── MàJ 7 : first-impression (avant shuffle pour que l'ordre qualité
    # soit préservé dans la graine déterministe) ──
    feed_ids = await apply_first_impression(user, feed_ids, config, db_session)

    # ── Shuffle déterministe ──────────────────────────────────────────
    feed_ids = shuffle_feed(
        feed_ids, user.id, datetime.now(timezone.utc).date()
    )

    return {
        "profile_ids": feed_ids,
        "wildcards": wildcards,
        "new_users": new_users,
    }


async def trace_pair(
    user_a_id: UUID,
    user_b_id: UUID,
    db_session: AsyncSession,
    redis_client: aioredis.Redis,
) -> dict:
    """
    Calcule la décomposition complète du score user_a → user_b.

    Endpoint admin only — utilisé pour comprendre pourquoi un profil
    apparaît (ou pas) dans le feed de user_a. Court-circuite les hard
    filters et la sélection de candidats : on score directement la paire.

    Retour :
        {
            "user_a": UUID, "user_b": UUID,
            "account_age_days": int,
            "weights": {"geo": float, "lifestyle": float, "behavior": float},
            "scores": {
                "geo": float,                    # 0-1
                "event_boost_pts": float,        # 0-15 (avant division /100)
                "geo_after_event_boost": float,
                "lifestyle": float,              # 0-1
                "behavior_multiplier": float,    # 0.6-1.4
                "behavior_effect": float,        # 1 + (m-1)*beh_w
                "final": float,                  # score combiné
            },
            "min_score_threshold": float,
            "passes_min_score": bool,
            "config_keys_used": list[str],
            "implicit_preferences_enabled": bool,
        }
    """
    user = await _load_user_full(user_a_id, db_session)
    if user is None or user.profile is None:
        return {"error": "user_a_not_found"}

    other = await _load_user_full(user_b_id, db_session)
    if other is None or other.profile is None:
        return {"error": "user_b_not_found"}

    created_at = user.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    account_age_days = (datetime.now(timezone.utc) - created_at).days

    config = await get_configs(_CONFIG_KEYS, redis_client, db_session)

    if user.city_id is not None:
        await load_proximity_cache(user.city_id, db_session)

    candidate_ids = [user_b_id]

    geo_scores = await compute_geo_scores(user, candidate_ids, config, db_session)
    geo_raw = geo_scores.get(user_b_id, 0.0)

    event_boosts = await compute_event_boosts(user.id, candidate_ids, db_session)
    event_pts = float(event_boosts.get(user_b_id, 0.0))
    geo_after = min(1.0, geo_raw + (event_pts / 100.0))

    lifestyle_scores = await compute_lifestyle_scores(
        user, candidate_ids, config, db_session
    )
    lifestyle = lifestyle_scores.get(user_b_id, 0.0)

    behavior_mults = await get_behavior_multipliers(
        candidate_ids, redis_client, db_session
    )
    m = behavior_mults.get(user_b_id, 1.0)

    geo_w, life_w, beh_w = await get_adaptive_weights(
        account_age_days, redis_client, db_session
    )
    behavior_effect = 1.0 + (m - 1.0) * beh_w

    today = datetime.now(timezone.utc).date()
    user_age = today.year - user.profile.birth_date.year - (
        (today.month, today.day)
        < (user.profile.birth_date.month, user.profile.birth_date.day)
    )
    other_age = today.year - other.profile.birth_date.year - (
        (today.month, today.day)
        < (other.profile.birth_date.month, other.profile.birth_date.day)
    )
    age_fit = compute_age_fit(
        user_age=user_age,
        candidate_age=other_age,
        user_seeking_age_min=user.profile.seeking_age_min,
        user_seeking_age_max=user.profile.seeking_age_max,
        candidate_seeking_age_min=other.profile.seeking_age_min,
        candidate_seeking_age_max=other.profile.seeking_age_max,
    )
    final = (geo_w * geo_after + life_w * lifestyle) * behavior_effect * age_fit

    return {
        "user_a": str(user_a_id),
        "user_b": str(user_b_id),
        "account_age_days": account_age_days,
        "weights": {"geo": geo_w, "lifestyle": life_w, "behavior": beh_w},
        "scores": {
            "geo": geo_raw,
            "event_boost_pts": event_pts,
            "geo_after_event_boost": geo_after,
            "lifestyle": lifestyle,
            "behavior_multiplier": m,
            "behavior_effect": behavior_effect,
            "age_fit": age_fit,
            "final": final,
        },
        "min_score_threshold": MATCHING_FEED_MIN_SCORE,
        "passes_min_score": final >= MATCHING_FEED_MIN_SCORE,
        "config_keys_used": list(_CONFIG_KEYS),
        "implicit_preferences_enabled": IMPLICIT_PREFERENCES_ENABLED,
    }


__all__ = ["generate_feed_for_user", "trace_pair"]
