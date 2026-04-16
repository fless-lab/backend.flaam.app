from __future__ import annotations

"""
L5 — Corrections (spec §6.4).

Wildcards, boost nouveaux profils, garantie de visibilité, shuffle
déterministe. Appliqués APRÈS la pondération L2/L3/L4 finale.
"""

import hashlib
import random
import struct
from datetime import date, datetime, timezone
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    NEW_USER_BOOST_BUCKETS,
    REDIS_VISIBILITY_KEY,
)
from app.models.user import User


# ── Wildcards ──


async def inject_wildcards(
    user: User,
    top_profiles: list[UUID],
    sorted_candidates: list[tuple[UUID, float]],
    geo_scores: dict[UUID, float],
    lifestyle_scores: dict[UUID, float],
    count: int,
    db_session: AsyncSession,
) -> list[UUID]:
    """
    Wildcards V1 (simple et testable).

    Critères :
      - NOT in top_profiles
      - geo_score >= médiane du pool (ancrage géographique)
      - lifestyle_score < 0.3 (lifestyle divergent)
    Tri : par (distance lifestyle) × (proximity géo) décroissante, pour
    récompenser les candidats proches physiquement mais différents.

    V2 (3-6 mois post-lancement, quand on aura des données réelles de
    feedback) : vector de profil appris depuis l'historique de likes,
    distance cosinus. Cf. spec §6.4 `compute_liked_profile_vector`.
    """
    if count <= 0 or not sorted_candidates:
        return []

    top_set = set(top_profiles)

    # Médiane des geo_scores sur l'ensemble du pool
    geo_values = sorted(geo_scores.values())
    if not geo_values:
        return []
    geo_median = geo_values[len(geo_values) // 2]

    wildcard_pool: list[tuple[UUID, float]] = []
    for cid, _ in sorted_candidates:
        if cid in top_set:
            continue
        g = geo_scores.get(cid, 0.0)
        l = lifestyle_scores.get(cid, 0.0)
        if g < geo_median:
            continue
        if l >= 0.3:
            continue
        # Ancrage géo fort × divergence lifestyle forte
        rank = g * (1.0 - l)
        wildcard_pool.append((cid, rank))

    wildcard_pool.sort(key=lambda x: x[1], reverse=True)
    return [cid for cid, _ in wildcard_pool[:count]]


# ── Boost nouveaux profils ──


async def apply_new_user_boost(
    remaining_candidates: list[UUID],
    max_count: int,
    db_session: AsyncSession,
) -> list[UUID]:
    """
    Sélectionne jusqu'à `max_count` nouveaux inscrits (0-10j) parmi les
    candidats restants, en respectant les buckets NEW_USER_BOOST_BUCKETS.

    Exclut les users qui ont déjà reset leur compte (account_created_count > 1)
    pour éviter le farm par re-création.
    """
    if max_count <= 0 or not remaining_candidates:
        return []

    rows = await db_session.execute(
        select(User.id, User.created_at, User.account_created_count).where(
            User.id.in_(remaining_candidates)
        )
    )
    user_data = {uid: (ca, cc) for uid, ca, cc in rows.all()}

    now = datetime.now(timezone.utc)
    picked: list[UUID] = []

    # Respecte l'ordre initial (pertinence décroissante de sorted_candidates)
    for cid in remaining_candidates:
        if len(picked) >= max_count:
            break
        info = user_data.get(cid)
        if info is None:
            continue
        created_at, created_count = info
        if created_count and created_count > 1:
            continue
        # Normaliser timezone (created_at peut être naive selon la DB)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_days = (now - created_at).days
        if any(lo <= age_days <= hi for lo, hi, _ in NEW_USER_BOOST_BUCKETS):
            picked.append(cid)

    return picked


# ── Garantie de visibilité ──


async def ensure_minimum_visibility(
    feed_ids: list[UUID],
    user: User,
    redis_client: aioredis.Redis,
    db_session: AsyncSession,
) -> list[UUID]:
    """
    Incrémente un compteur Redis `visibility:{candidate_id}` pour tracer le
    nombre de fois où un profil est apparu dans un feed.

    V1 : passthrough — on ne réordonne pas encore. La réinjection active
    des profils sous-exposés demande une requête inverse (« quels candidats
    en dessous du seuil dans cette ville ? ») qui sort du scope S5.
    TODO S10 : implémenter la réinjection forcée à partir du compteur.
    """
    if not feed_ids:
        return feed_ids

    pipe = redis_client.pipeline()
    for cid in feed_ids:
        pipe.incr(REDIS_VISIBILITY_KEY.format(user_id=str(cid)))
    await pipe.execute()
    return feed_ids


# ── Shuffle déterministe ──


def shuffle_feed(
    feed_ids: list[UUID], user_id: UUID, target_date: date
) -> list[UUID]:
    """
    Shuffle déterministe seedé par (user_id, date). Empêche le
    refresh-pour-reorder : le même user voit le même ordre toute la journée.
    """
    seed_str = f"{user_id}:{target_date.isoformat()}"
    seed_bytes = hashlib.sha256(seed_str.encode()).digest()
    seed_int = struct.unpack("<I", seed_bytes[:4])[0]

    rng = random.Random(seed_int)
    shuffled = list(feed_ids)
    rng.shuffle(shuffled)
    return shuffled


__all__ = [
    "inject_wildcards",
    "apply_new_user_boost",
    "ensure_minimum_visibility",
    "shuffle_feed",
]
