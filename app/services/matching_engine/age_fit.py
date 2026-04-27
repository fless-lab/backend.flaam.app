from __future__ import annotations

"""
Multiplicateur soft d'ajustement d'âge.

Le hard filter laisse passer ±N ans hors range (cf. MATCHING_AGE_OVERLAP_YEARS).
Cette fonction calcule un multiplicateur ∈ [1 - N×penalty, 1.0] appliqué au
score final pour pénaliser progressivement les candidats hors-range.

Bilatéral : on considère la pire des 2 distances (user→candidate ET
candidate→user). Si les 2 sont dans le range strict → 1.0. Si l'un dévie de
2 ans → multiplicateur = 1 - 2×0.20 = 0.60.
"""

from app.core.constants import (
    MATCHING_AGE_FIT_PENALTY_PER_YEAR,
    MATCHING_AGE_OVERLAP_YEARS,
)


def _years_outside(age: int, lo: int, hi: int) -> int:
    if lo <= age <= hi:
        return 0
    if age < lo:
        return lo - age
    return age - hi


def compute_age_fit(
    user_age: int,
    candidate_age: int,
    user_seeking_age_min: int,
    user_seeking_age_max: int,
    candidate_seeking_age_min: int,
    candidate_seeking_age_max: int,
) -> float:
    """
    Renvoie un multiplicateur ∈ [floor, 1.0] où floor = 1 - N×penalty.

    Avec N=3 et penalty=0.20 :
      0y hors range → 1.00
      1y hors range → 0.80
      2y hors range → 0.60
      3y hors range → 0.40

    On prend max() des 2 distances (user→cand, cand→user) — on pénalise
    selon le pire des 2 côtés, pas le cumul.
    """
    d_user_to_cand = _years_outside(
        candidate_age, user_seeking_age_min, user_seeking_age_max
    )
    d_cand_to_user = _years_outside(
        user_age, candidate_seeking_age_min, candidate_seeking_age_max
    )
    worst = max(d_user_to_cand, d_cand_to_user)
    if worst == 0:
        return 1.0
    # Clamp défensif : le hard filter empêche déjà worst > overlap, mais
    # un seed bizarre pourrait passer.
    worst = min(worst, MATCHING_AGE_OVERLAP_YEARS)
    return max(0.0, 1.0 - worst * MATCHING_AGE_FIT_PENALTY_PER_YEAR)
