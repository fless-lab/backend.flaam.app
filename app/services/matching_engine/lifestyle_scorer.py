from __future__ import annotations

"""
L3 — Score lifestyle (spec §6.3b).

Composantes :
  1. Tags : Jaccard simple pondéré par lifestyle_w_tags
  2. Intentions : lookup dans INTENTION_COMPATIBILITY_MATRIX
  3. Rythme : early_bird / night_owl / None (neutre)
  4. Langues : bonus si au moins 1 langue commune

Signature :
    Input  : user (User, profile loaded), candidate_ids (list[UUID]),
             config (dict[str, float]), db_session (AsyncSession)
    Output : dict[UUID, float] — scores normalisés 0-1.

Pas de side effects.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import INTENTION_COMPATIBILITY_MATRIX
from app.models.profile import Profile


async def _load_candidate_profiles(
    candidate_ids: list[UUID], db_session: AsyncSession
) -> dict[UUID, Profile]:
    if not candidate_ids:
        return {}
    rows = await db_session.execute(
        select(Profile).where(Profile.user_id.in_(candidate_ids))
    )
    return {p.user_id: p for p in rows.scalars()}


def _tags_jaccard(user_tags: list | None, cand_tags: list | None) -> float:
    u = set(user_tags or [])
    c = set(cand_tags or [])
    if not u or not c:
        return 0.0
    inter = u & c
    union = u | c
    if not union:
        return 0.0
    return len(inter) / len(union)


def _intention_score(user_intention: str | None, cand_intention: str | None) -> float:
    if not user_intention or not cand_intention:
        return 0.5  # données manquantes → neutre
    return INTENTION_COMPATIBILITY_MATRIX.get(user_intention, {}).get(
        cand_intention, 0.5
    )


async def compute_lifestyle_scores(
    user,
    candidate_ids: list[UUID],
    config: dict[str, float],
    db_session: AsyncSession,
) -> dict[UUID, float]:
    """
    Retourne {candidate_id: score ∈ [0, 1]}.
    """
    if not candidate_ids:
        return {}
    profile = user.profile
    if profile is None:
        return {cid: 0.0 for cid in candidate_ids}

    cand_profiles = await _load_candidate_profiles(candidate_ids, db_session)

    # Lifestyle score : tags (couleur) + intention (contrat).
    # rhythm et languages ont été retirés du scoring : rhythm n'existe
    # plus en DB ; languages reste comme champ d'affichage non scoré.
    w_tags = config.get("lifestyle_w_tags", 0.35)
    w_int = config.get("lifestyle_w_intention", 0.65)

    scores: dict[UUID, float] = {}
    for cid in candidate_ids:
        cand = cand_profiles.get(cid)
        if cand is None:
            scores[cid] = 0.0
            continue

        raw = (
            w_tags * _tags_jaccard(profile.tags, cand.tags)
            + w_int * _intention_score(profile.intention, cand.intention)
        )
        scores[cid] = max(0.0, min(1.0, raw))

    return scores


__all__ = ["compute_lifestyle_scores"]
