from __future__ import annotations

"""
L2 — Score géo (spec §6.3).

Composantes :
  1. Score quartier en 3 passes :
     - Passe 1 : exact match (même quartier)
     - Passe 2 : soft match via graphe de proximité (seuil configurable)
     - Passe 3 : interested match (l'un coche "interested", l'autre y est)
  2. Score spots : Jaccard pondéré par social_weight de catégorie
  3. Bonus fidélité : moyenne géométrique des fidelity_score sur spots communs
  4. Fraîcheur : decay exponentiel des check-ins

Signature :
    Input  : user (User, avec user_quartiers+user_spots loadés),
             candidate_ids (list[UUID]), config (dict[str, float]),
             db_session (AsyncSession)
    Output : dict[UUID, float] — scores normalisés 0-1.

Aucun side effect sauf le chargement du cache proximity (au démarrage).
"""

import math
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import (
    QUARTIER_RELATION_WEIGHT_KEYS,
    SPOT_SOCIAL_WEIGHTS,
)
from app.models.quartier import Quartier
from app.models.quartier_proximity import QuartierProximity
from app.models.user_quartier import UserQuartier
from app.models.user_spot import UserSpot


# ── Cache module-level du graphe de proximité ──
# Format : {(quartier_a_id, quartier_b_id): score}. Symétrique.
# Chargé une fois par batch (et par ville). Bien plus rapide qu'un
# JOIN à chaque user (cf. commentaire Session 5, complément C).
_proximity_cache: dict[tuple[UUID, UUID], float] = {}
_proximity_cache_loaded_for: UUID | None = None


async def load_proximity_cache(city_id: UUID, db_session: AsyncSession) -> None:
    """
    Charge le graphe de proximité d'une ville en mémoire.
    Idempotent : si déjà chargé pour la même ville, no-op.
    Utiliser `reset_proximity_cache()` pour forcer un rechargement.
    """
    global _proximity_cache, _proximity_cache_loaded_for
    if _proximity_cache_loaded_for == city_id and _proximity_cache:
        return

    stmt = (
        select(
            QuartierProximity.quartier_a_id,
            QuartierProximity.quartier_b_id,
            QuartierProximity.proximity_score,
        )
        .join(Quartier, Quartier.id == QuartierProximity.quartier_a_id)
        .where(Quartier.city_id == city_id)
    )
    rows = await db_session.execute(stmt)

    _proximity_cache = {}
    for a_id, b_id, score in rows.all():
        _proximity_cache[(a_id, b_id)] = float(score)
        _proximity_cache[(b_id, a_id)] = float(score)
    _proximity_cache_loaded_for = city_id


def reset_proximity_cache() -> None:
    """Force un rechargement au prochain appel. Utilisé dans les tests."""
    global _proximity_cache, _proximity_cache_loaded_for
    _proximity_cache = {}
    _proximity_cache_loaded_for = None


def get_proximity(q1: UUID, q2: UUID) -> float:
    """1.0 si même quartier, sinon valeur du graphe ou 0.0."""
    if q1 == q2:
        return 1.0
    return _proximity_cache.get((q1, q2), 0.0)


# ── Chargement batch des données candidats ──


async def _load_candidates_quartiers(
    candidate_ids: list[UUID], db_session: AsyncSession
) -> dict[UUID, dict[UUID, str]]:
    """
    Retourne {user_id: {quartier_id: relation_type}} pour tous les candidats.
    """
    if not candidate_ids:
        return {}
    stmt = select(UserQuartier).where(
        UserQuartier.user_id.in_(candidate_ids),
        UserQuartier.is_active_in_matching.is_(True),
    )
    rows = await db_session.execute(stmt)
    out: dict[UUID, dict[UUID, str]] = {}
    for uq in rows.scalars():
        out.setdefault(uq.user_id, {})[uq.quartier_id] = uq.relation_type
    return out


async def _load_candidates_spots(
    candidate_ids: list[UUID], db_session: AsyncSession
) -> dict[UUID, dict[UUID, UserSpot]]:
    """
    Retourne {user_id: {spot_id: UserSpot}} avec spot.category pré-chargée.
    """
    if not candidate_ids:
        return {}
    stmt = (
        select(UserSpot)
        .options(selectinload(UserSpot.spot))
        .where(
            UserSpot.user_id.in_(candidate_ids),
            UserSpot.is_active_in_matching.is_(True),
        )
    )
    rows = await db_session.execute(stmt)
    out: dict[UUID, dict[UUID, UserSpot]] = {}
    for us in rows.scalars():
        out.setdefault(us.user_id, {})[us.spot_id] = us
    return out


# ── Sous-scores ──


def _relation_weight(relation_type: str, config: dict[str, float]) -> float:
    key = QUARTIER_RELATION_WEIGHT_KEYS.get(relation_type)
    if key is None:
        return 0.5
    return config.get(key, 0.5)


def _quartier_score_unified(
    user_physical: dict[UUID, str],
    user_interested: dict[UUID, str],
    candidate_physical: dict[UUID, str],
    candidate_interested: dict[UUID, str],
    config: dict[str, float],
) -> float:
    """
    Score quartier simplifié pour la beta.

    Approche :
      1. Set unifié de tous les quartiers déclarés par chaque user
         (lives + works + hangs + interested mélangés)
      2. Jaccard sur le set unifié = base
      3. Bonus signal fort si overlap sur lives ou works
         (signaux qui matchent vraiment la vie réelle, pas juste une
         préférence)

    Pourquoi : avec un pool faible (beta Lomé ~50 users), granulariser
    à 4 relations dilue le signal. Un user qui déclare beaucoup de
    quartiers ne doit pas être pénalisé (Jaccard pur ferait baisser
    le score). Le bonus garde le signal fort sans complexité de poids.

    Activable via env `geo_unified_set_enabled = 1.0`.
    """
    user_set = set(user_physical.keys()) | set(user_interested.keys())
    cand_set = set(candidate_physical.keys()) | set(candidate_interested.keys())

    if not user_set or not cand_set:
        return 0.0

    inter = user_set & cand_set
    union = user_set | cand_set
    base = len(inter) / len(union) if union else 0.0

    bonus = 0.0
    user_lives = {q for q, r in user_physical.items() if r == "lives"}
    cand_lives = {q for q, r in candidate_physical.items() if r == "lives"}
    if user_lives & cand_lives:
        bonus += config.get("geo_unified_bonus_lives", 0.30)

    user_works = {q for q, r in user_physical.items() if r == "works"}
    cand_works = {q for q, r in candidate_physical.items() if r == "works"}
    if user_works & cand_works:
        bonus += config.get("geo_unified_bonus_works", 0.20)

    return min(1.0, base + bonus)


def _quartier_score_with_proximity(
    user_physical: dict[UUID, str],
    user_interested: dict[UUID, str],
    candidate_physical: dict[UUID, str],
    config: dict[str, float],
) -> float:
    """
    Score quartier normalisé 0-1. Voir spec §6.3 pour les 3 passes.
    Algo legacy — gardé en fallback (geo_unified_set_enabled = 0.0).
    """
    if not candidate_physical:
        return 0.0
    if not user_physical and not user_interested:
        return 0.0

    threshold = config.get("geo_proximity_threshold", 0.40)

    total_score = 0.0
    matched: set[UUID] = set()

    # Poids maximum possible : chaque quartier du plus gros des deux côtés
    # peut matcher à "lives" (poids max). Utilisé pour normaliser.
    lives_weight = config.get("geo_w_quartier_lives", 2.0)

    # ── Passe 1 : exact match ──
    exact_common = set(user_physical.keys()) & set(candidate_physical.keys())
    for qid in exact_common:
        w_user = _relation_weight(user_physical[qid], config)
        w_cand = _relation_weight(candidate_physical[qid], config)
        total_score += max(w_user, w_cand)
        matched.add(qid)

    # ── Passe 2 : soft match via proximité ──
    for user_qid, user_rtype in user_physical.items():
        if user_qid in exact_common:
            continue
        best_contribution = 0.0
        best_cand_qid: UUID | None = None
        for cand_qid, cand_rtype in candidate_physical.items():
            if cand_qid in matched:
                continue
            proximity = get_proximity(user_qid, cand_qid)
            if proximity < threshold:
                continue
            w_user = _relation_weight(user_rtype, config)
            w_cand = _relation_weight(cand_rtype, config)
            contribution = max(w_user, w_cand) * proximity
            if proximity < 0.65:
                # Proches mais pas voisins → pénalité pour éviter la surpondération
                contribution *= 0.5
            if contribution > best_contribution:
                best_contribution = contribution
                best_cand_qid = cand_qid
        if best_cand_qid is not None:
            total_score += best_contribution
            matched.add(best_cand_qid)

    # ── Passe 3 : interested match ──
    interested_weight = config.get("geo_w_quartier_interested", 0.8)
    for int_qid in user_interested:
        if int_qid in matched:
            continue
        if int_qid in candidate_physical:
            w_cand = _relation_weight(candidate_physical[int_qid], config)
            total_score += min(interested_weight, w_cand)
            matched.add(int_qid)

    # Normalisation : on divise par (nb quartiers dans l'union) × poids_max.
    all_quartiers = (
        set(user_physical.keys())
        | set(user_interested.keys())
        | set(candidate_physical.keys())
    )
    if not all_quartiers:
        return 0.0
    denom = len(all_quartiers) * lives_weight
    return max(0.0, min(1.0, total_score / denom))


def _spot_overlap(
    user_spots: dict[UUID, UserSpot],
    candidate_spots: dict[UUID, UserSpot],
) -> float:
    """Nombre de spots communs pondéré par le social_weight de leur catégorie."""
    if not user_spots or not candidate_spots:
        return 0.0
    common = set(user_spots.keys()) & set(candidate_spots.keys())
    if not common:
        return 0.0
    weighted = 0.0
    for sid in common:
        cat = user_spots[sid].spot.category if user_spots[sid].spot else None
        w = SPOT_SOCIAL_WEIGHTS.get(cat, 1.0) if cat else 1.0
        weighted += w
    denom = max(len(user_spots), len(candidate_spots))
    return min(1.0, weighted / denom)


def _fidelity_bonus(
    user_spots: dict[UUID, UserSpot],
    candidate_spots: dict[UUID, UserSpot],
) -> float:
    """
    Moyenne géométrique des fidelity_score sur les spots communs.
    Récompense les paires où les deux sont habitués.
    """
    common = set(user_spots.keys()) & set(candidate_spots.keys())
    if not common:
        return 0.0
    total = 0.0
    for sid in common:
        a = user_spots[sid].fidelity_score or 0.0
        b = candidate_spots[sid].fidelity_score or 0.0
        total += math.sqrt(max(0.0, a) * max(0.0, b))
    return min(1.0, total / len(common))


def _freshness_score(
    user_spots: dict[UUID, UserSpot],
    candidate_spots: dict[UUID, UserSpot],
    halflife_days: float,
) -> float:
    """
    Decay exponentiel basé sur candidate.last_checkin_at (on juge le candidat).
    Si pas de spots communs → score neutre 0.5.
    """
    common = set(user_spots.keys()) & set(candidate_spots.keys())
    if not common:
        return 0.5
    now = datetime.now(timezone.utc)
    total = 0.0
    for sid in common:
        last = candidate_spots[sid].last_checkin_at
        if last is None:
            total += 0.3
            continue
        days = (now - last).days
        total += math.exp(-0.693 * days / max(1e-6, halflife_days))
    return min(1.0, total / len(common))


# ── Fonction publique ──


async def compute_geo_scores(
    user,
    candidate_ids: list[UUID],
    config: dict[str, float],
    db_session: AsyncSession,
) -> dict[UUID, float]:
    """
    Retourne {candidate_id: score ∈ [0, 1]} pour chaque candidat.
    L'utilisateur doit avoir user_quartiers et user_spots pré-chargés.
    """
    if not candidate_ids:
        return {}

    # Données user — filtrées par is_active_in_matching (gel doux premium)
    user_quartiers = {
        uq.quartier_id: uq.relation_type
        for uq in (user.user_quartiers or [])
        if uq.is_active_in_matching
    }
    user_physical = {
        qid: rt for qid, rt in user_quartiers.items() if rt != "interested"
    }
    user_interested = {
        qid: rt for qid, rt in user_quartiers.items() if rt == "interested"
    }
    user_spots = {
        us.spot_id: us
        for us in (user.user_spots or [])
        if us.is_active_in_matching
    }

    # Données candidats (batch)
    cand_quartiers = await _load_candidates_quartiers(candidate_ids, db_session)
    cand_spots = await _load_candidates_spots(candidate_ids, db_session)

    # feed_search_mode des candidats — pour score géo neutre 0.5 quand
    # un user (viewer ou candidat) est en mode "toute la ville".
    from app.models.user import User as _User
    mode_rows = await db_session.execute(
        select(_User.id, _User.feed_search_mode).where(
            _User.id.in_(candidate_ids),
        ),
    )
    cand_search_mode: dict[UUID, str] = {
        row[0]: row[1] for row in mode_rows.all()
    }
    user_mode = getattr(user, "feed_search_mode", "whole_city") or "whole_city"

    w_q = config.get("geo_w_quartier", 0.45)
    w_s = config.get("geo_w_spot", 0.30)
    w_f = config.get("geo_w_fidelity", 0.15)
    w_fr = config.get("geo_w_freshness", 0.10)
    halflife = config.get("freshness_decay_halflife_days", 30.0)

    use_unified = config.get("geo_unified_set_enabled", 1.0) >= 0.5

    scores: dict[UUID, float] = {}
    for cid in candidate_ids:
        cq = cand_quartiers.get(cid, {})
        cq_physical = {qid: rt for qid, rt in cq.items() if rt != "interested"}
        cq_interested = {qid: rt for qid, rt in cq.items() if rt == "interested"}
        cs = cand_spots.get(cid, {})
        cand_mode = cand_search_mode.get(cid, "whole_city")

        # Score géo neutre 0.5 si l'un des deux est en "whole_city" :
        # l'user signale "la géo ne classe pas pour moi". Bonus lives/works
        # restent actifs (ce sont des facts, pas des préférences).
        if user_mode == "whole_city" or cand_mode == "whole_city":
            base = 0.5
            bonus = 0.0
            user_lives = {qid for qid, rt in user_physical.items() if rt == "lives"}
            cand_lives = {qid for qid, rt in cq_physical.items() if rt == "lives"}
            if user_lives & cand_lives:
                bonus += config.get("geo_unified_bonus_lives", 0.30)
            user_works = {qid for qid, rt in user_physical.items() if rt == "works"}
            cand_works = {qid for qid, rt in cq_physical.items() if rt == "works"}
            if user_works & cand_works:
                bonus += config.get("geo_unified_bonus_works", 0.20)
            q_score = min(1.0, base + bonus)
        elif use_unified:
            q_score = _quartier_score_unified(
                user_physical, user_interested,
                cq_physical, cq_interested,
                config,
            )
        else:
            q_score = _quartier_score_with_proximity(
                user_physical, user_interested, cq_physical, config
            )
        s_score = _spot_overlap(user_spots, cs)
        f_score = _fidelity_bonus(user_spots, cs)
        fr_score = _freshness_score(user_spots, cs, halflife)

        raw = w_q * q_score + w_s * s_score + w_f * f_score + w_fr * fr_score
        # Les poids ne somment pas forcément à 1 → on clamp en sécurité.
        scores[cid] = max(0.0, min(1.0, raw))

    return scores


__all__ = [
    "load_proximity_cache",
    "reset_proximity_cache",
    "get_proximity",
    "compute_geo_scores",
]
