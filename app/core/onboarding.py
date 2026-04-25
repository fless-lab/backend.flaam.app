from __future__ import annotations

"""
Machine à états de l'onboarding (spec §13).

L'état courant est persisté dans `users.onboarding_step`. Le client
reçoit l'état complet via `GET /profiles/me/onboarding`.

Les champs `user` et `profile` permettent de déduire automatiquement
quelle étape est déjà satisfaite (sans se fier aveuglément à la valeur
stockée), ce qui rend le calcul idempotent.
"""

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.photo import Photo
    from app.models.profile import Profile
    from app.models.user import User


class OnboardingStep(str, Enum):
    """
    Flow simplifié — 7 étapes bloquantes uniquement. Tout le reste
    (bio, spots, sector, prompts, quartiers additionnels) est déplacé
    en édition de profil avec des nudges contextuels (1er match, 1er
    check-in...).
    """
    # ── Flow d'onboarding (7 étapes, dans l'ordre) ──
    PHONE_VERIFIED = "phone_verified"
    BASIC_INFO = "basic_info"               # display_name + birth_date + gender + seeking_gender
    SELFIE_VERIFICATION = "selfie_verification"
    SEARCH_AREA = "search_area"             # ville + quartier "lives" (1 seul écran mobile)
    PHOTOS = "photos"                       # min 2, max 3 (free) / 6 (premium)
    INTENTION = "intention"
    TAGS = "tags"                           # max 8 (inchangé)

    # ── Terminé ──
    COMPLETED = "completed"

    # ── Steps gardés en enum pour data legacy / édition profil ──
    # Plus jamais dans le flow. Le mobile les expose dans EditProfile /
    # Settings ou via des nudges contextuels.
    CITY_SELECTION = "city_selection"       # legacy — fusionné dans SEARCH_AREA
    QUARTIERS = "quartiers"                 # legacy — fusionné dans SEARCH_AREA, l'edit profil ajoute des quartiers additionnels
    BIO = "bio"                             # nudge après 1er match
    SPOTS = "spots"                         # nudge après 1er check-in
    SECTOR = "sector"                       # optionnel, settings
    PROMPTS = "prompts"                     # déprécié, code conservé pour réactivation future
    NOTIFICATION_PERMISSION = "notification_permission"  # géré côté mobile au 1er render du feed


ONBOARDING_FLOW: list[OnboardingStep] = [
    OnboardingStep.PHONE_VERIFIED,
    OnboardingStep.BASIC_INFO,
    OnboardingStep.SELFIE_VERIFICATION,
    OnboardingStep.SEARCH_AREA,
    OnboardingStep.PHOTOS,
    OnboardingStep.INTENTION,
    OnboardingStep.TAGS,
    OnboardingStep.COMPLETED,
]

SKIPPABLE_STEPS: set[OnboardingStep] = {
    # Aucune étape skippable dans l'onboarding minimal — tout est
    # bloquant. Les enrichissements skippables (bio, spots, sector...)
    # sont en édition profil, pas dans le flow.
}

# Poids pour le score de complétion (§13).
# Le score reflète l'état COMPLET du profil (onboarding + enrichissements
# édit profil), pas seulement le flow d'inscription. Total = 1.0.
STEP_COMPLETENESS_WEIGHT: dict[str, float] = {
    # Onboarding (cumulé : 0.85)
    OnboardingStep.BASIC_INFO.value: 0.0,
    OnboardingStep.SELFIE_VERIFICATION.value: 0.15,
    OnboardingStep.SEARCH_AREA.value: 0.15,
    OnboardingStep.PHOTOS.value: 0.30,
    OnboardingStep.INTENTION.value: 0.10,
    OnboardingStep.TAGS.value: 0.15,
    # Enrichissements (cumulé : 0.15) — édit profil, débloque la
    # complétude maximum mais pas l'accès au feed.
    OnboardingStep.BIO.value: 0.10,
    OnboardingStep.SPOTS.value: 0.05,
    # Désactivés / legacy (poids 0)
    OnboardingStep.SECTOR.value: 0.0,
    OnboardingStep.PROMPTS.value: 0.0,
    OnboardingStep.QUARTIERS.value: 0.0,
    OnboardingStep.CITY_SELECTION.value: 0.0,
}


def _photos_count(photos: list["Photo"] | None) -> int:
    return len(photos) if photos else 0


def _step_index(step: OnboardingStep) -> int:
    """Index of a step in ONBOARDING_FLOW."""
    return ONBOARDING_FLOW.index(step)


def _is_step_passed(step: OnboardingStep, user: "User") -> bool:
    """True if user.onboarding_step is strictly after this step in the flow.

    This covers skipped steps: the skip endpoint advances
    user.onboarding_step past the skipped step, so any step before
    the current position is either completed or skipped.
    """
    try:
        current = OnboardingStep(user.onboarding_step)
    except ValueError:
        return False
    return _step_index(current) > _step_index(step)


def is_step_done(
    step: OnboardingStep,
    user: "User",
    profile: "Profile | None",
) -> bool:
    """True si l'étape est déjà satisfaite (ou a été skippée)."""
    # If onboarding has already advanced past this step, it's done
    if _is_step_passed(step, user):
        return True

    if step is OnboardingStep.CITY_SELECTION:
        return user.city_id is not None
    if step is OnboardingStep.PHONE_VERIFIED:
        return user.is_phone_verified
    if step is OnboardingStep.BASIC_INFO:
        return profile is not None and bool(profile.display_name)
    if step is OnboardingStep.SELFIE_VERIFICATION:
        return user.is_selfie_verified
    if step is OnboardingStep.PHOTOS:
        return _photos_count(user.photos) >= 2
    if step is OnboardingStep.SEARCH_AREA:
        # Ville obligatoire. Les quartiers sont OPTIONNELS — l'utilisateur
        # peut choisir "toute la ville" (0 quartier) ou un set de quartiers
        # (1+ UserQuartier relation_type='lives'). Le geo_scorer s'adapte.
        return user.city_id is not None
    if step is OnboardingStep.QUARTIERS:
        return any(
            uq.relation_type == "lives" for uq in (user.user_quartiers or [])
        )
    if step is OnboardingStep.INTENTION:
        return profile is not None and bool(profile.intention)
    if step is OnboardingStep.SECTOR:
        return profile is not None and bool(profile.sector)
    if step is OnboardingStep.BIO:
        return profile is not None and bool(
            profile.bio and profile.bio.strip()
        )
    if step is OnboardingStep.PROMPTS:
        return profile is not None and bool(profile.prompts)
    if step is OnboardingStep.TAGS:
        return profile is not None and bool(profile.tags)
    if step is OnboardingStep.SPOTS:
        return bool(user.user_spots)
    if step is OnboardingStep.NOTIFICATION_PERMISSION:
        return user.notification_prefs is not None
    if step is OnboardingStep.COMPLETED:
        return user.onboarding_step == OnboardingStep.COMPLETED.value
    return False


def next_step(user: "User", profile: "Profile | None") -> OnboardingStep:
    """Première étape non satisfaite du flow."""
    for step in ONBOARDING_FLOW:
        if step is OnboardingStep.COMPLETED:
            return step
        if not is_step_done(step, user, profile):
            return step
    return OnboardingStep.COMPLETED


def advance_onboarding(user: "User") -> bool:
    """
    Met à jour `users.onboarding_step` vers la première étape non
    satisfaite. Retourne True si la valeur a changé (pour que
    l'appelant commit éventuellement).
    """
    target = next_step(user, user.profile)
    if user.onboarding_step != target.value:
        user.onboarding_step = target.value
        return True
    return False


def compute_completeness(
    user: "User", profile: "Profile | None"
) -> tuple[float, list[dict]]:
    """
    Retourne (score, breakdown). Le score est clampé à 1.0.

    Breakdown = liste [{step, weight, achieved}] pour chaque étape
    pondérée (poids > 0).
    """
    score = 0.0
    breakdown: list[dict] = []
    for step_name, weight in STEP_COMPLETENESS_WEIGHT.items():
        if weight <= 0:
            continue
        step = OnboardingStep(step_name)
        achieved = is_step_done(step, user, profile)
        if achieved:
            score += weight
        breakdown.append(
            {"step": step_name, "weight": weight, "achieved": achieved}
        )
    return min(score, 1.0), breakdown


__all__ = [
    "OnboardingStep",
    "ONBOARDING_FLOW",
    "SKIPPABLE_STEPS",
    "STEP_COMPLETENESS_WEIGHT",
    "is_step_done",
    "next_step",
    "advance_onboarding",
    "compute_completeness",
]
