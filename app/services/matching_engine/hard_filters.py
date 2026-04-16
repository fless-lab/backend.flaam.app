from __future__ import annotations

"""
L1 — Filtres durs.

Élimine 70-85% des candidats en une seule requête SQL. Pas de score, que
des filtres binaires.

Signature :
    Input  : user (User), db_session (AsyncSession)
    Output : list[UUID] — IDs des candidats qui passent tous les filtres.

Aucun side effect.
"""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, exists, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    INTENTION_COMPATIBILITY_MATRIX,
    MATCHING_ACTIVE_WINDOW_DAYS,
    MATCHING_SKIP_COOLDOWN_DAYS,
)
from app.models.block import Block
from app.models.contact_blacklist import ContactBlacklist
from app.models.match import Match
from app.models.profile import Profile
from app.models.user import User


# Seuil strict en dessous duquel on considère les intentions incompatibles
# (la matrice retourne 0.1 pour serious↔friendship : on exclut ce genre
# de pairs au niveau L1 pour ne pas polluer les feeds).
_INTENTION_HARD_THRESHOLD = 0.3


def _compatible_intention_pairs() -> list[tuple[str, str]]:
    """Retourne la liste des pairs (user, candidate) acceptées en L1."""
    return [
        (a, b)
        for a, row in INTENTION_COMPATIBILITY_MATRIX.items()
        for b, score in row.items()
        if score >= _INTENTION_HARD_THRESHOLD
    ]


def _candidates_intentions_for(user_intention: str) -> list[str]:
    return [
        b
        for b, score in INTENTION_COMPATIBILITY_MATRIX.get(user_intention, {}).items()
        if score >= _INTENTION_HARD_THRESHOLD
    ]


def _seeking_gender_match(
    user_seeking: str, user_gender: str, candidate_gender_col, candidate_seeking_col
):
    """
    Compatibilité de genre bidirectionnelle :
    - user.seeking_gender accepte candidate.gender
    - candidate.seeking_gender accepte user.gender
    """
    # ── user → candidate ──
    if user_seeking == "men":
        user_to_cand = candidate_gender_col == "man"
    elif user_seeking == "women":
        user_to_cand = candidate_gender_col == "woman"
    else:  # everyone
        user_to_cand = candidate_gender_col.in_(("man", "woman", "non_binary"))

    # ── candidate → user ──
    if user_gender == "man":
        cand_to_user = candidate_seeking_col.in_(("men", "everyone"))
    elif user_gender == "woman":
        cand_to_user = candidate_seeking_col.in_(("women", "everyone"))
    else:  # non_binary : seul "everyone" matche
        cand_to_user = candidate_seeking_col == "everyone"

    return and_(user_to_cand, cand_to_user)


def _calculate_age(birth: date, today: date) -> int:
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


async def apply_hard_filters(
    user: User,
    db_session: AsyncSession,
) -> list[UUID]:
    """
    Retourne les IDs des candidats valides pour `user`.

    Filtres appliqués :
      1. Même ville
      2. Pas soi-même
      3. Actif, visible, non banni, non soft-deleted
      4. last_active_at >= now - 7j
      5. is_selfie_verified
      6. Genre compatible bidirectionnel
      7. Âge compatible bidirectionnel
      8. Intention compatible (matrice ≥ 0.3)
      9. Pas bloqué dans un sens ni dans l'autre
     10. Pas dans la blacklist contacts
     11. Pas skippé dans les 30 derniers jours
     12. Pas déjà matché (pending/matched)
    """
    profile = user.profile
    if profile is None or user.city_id is None:
        return []

    now = datetime.now(timezone.utc)
    today = now.date()
    skip_cutoff = now - timedelta(days=MATCHING_SKIP_COOLDOWN_DAYS)
    active_cutoff = now - timedelta(days=MATCHING_ACTIVE_WINDOW_DAYS)
    user_age = _calculate_age(profile.birth_date, today)

    # ── Bornes dates pour l'âge du candidat ──
    # candidate_age ∈ [user.seeking_age_min, user.seeking_age_max]
    # ⇒ birth_date ∈ [today - (max+1)y + 1j, today - min y]
    max_birth = date(today.year - profile.seeking_age_min, today.month, today.day)
    min_birth = date(
        today.year - profile.seeking_age_max - 1, today.month, today.day
    ) + timedelta(days=1)

    allowed_intentions = _candidates_intentions_for(profile.intention)
    if not allowed_intentions:
        return []

    stmt = (
        select(User.id)
        .join(Profile, Profile.user_id == User.id)
        .where(
            and_(
                User.city_id == user.city_id,
                User.id != user.id,
                User.is_active.is_(True),
                User.is_visible.is_(True),
                User.is_banned.is_(False),
                User.is_deleted.is_(False),
                User.is_selfie_verified.is_(True),
                User.last_active_at >= active_cutoff,
                # Âge candidat dans la fourchette du user
                Profile.birth_date >= min_birth,
                Profile.birth_date <= max_birth,
                # Âge du user dans la fourchette cherchée par le candidat
                Profile.seeking_age_min <= user_age,
                Profile.seeking_age_max >= user_age,
                # Genre bidir
                _seeking_gender_match(
                    profile.seeking_gender,
                    profile.gender,
                    Profile.gender,
                    Profile.seeking_gender,
                ),
                # Intention compatible
                Profile.intention.in_(allowed_intentions),
                # Blocks dans les deux sens
                not_(
                    exists().where(
                        and_(
                            Block.blocker_id == user.id,
                            Block.blocked_id == User.id,
                        )
                    )
                ),
                not_(
                    exists().where(
                        and_(
                            Block.blocker_id == User.id,
                            Block.blocked_id == user.id,
                        )
                    )
                ),
                # Blacklist contacts (par phone_hash)
                not_(
                    exists().where(
                        and_(
                            ContactBlacklist.user_id == user.id,
                            ContactBlacklist.phone_hash == User.phone_hash,
                        )
                    )
                ),
                # Pas skippé dans les 30j
                not_(
                    exists().where(
                        and_(
                            Match.user_a_id == user.id,
                            Match.user_b_id == User.id,
                            Match.status == "skipped",
                            Match.created_at >= skip_cutoff,
                        )
                    )
                ),
                # Pas déjà matché (dans un sens ou l'autre)
                not_(
                    exists().where(
                        and_(
                            Match.status.in_(("pending", "matched")),
                            (
                                and_(
                                    Match.user_a_id == user.id,
                                    Match.user_b_id == User.id,
                                )
                                | and_(
                                    Match.user_a_id == User.id,
                                    Match.user_b_id == user.id,
                                )
                            ),
                        )
                    )
                ),
            )
        )
    )

    result = await db_session.execute(stmt)
    return [row[0] for row in result.all()]


__all__ = ["apply_hard_filters"]
