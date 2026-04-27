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
    PROMPTS = "prompts"                     # affichage uniquement, max 3, palier 3% par prompt
    LANGUAGES = "languages"                 # affichage uniquement, ≥1 langue
    NOTIFICATION_PERMISSION = "notification_permission"  # géré côté mobile au 1er render du feed


ONBOARDING_FLOW: list[OnboardingStep] = [
    OnboardingStep.PHONE_VERIFIED,
    # Selfie en 2ème : on vérifie que c'est une vraie personne avant
    # de demander le moindre détail perso. Coût psy faible (1 photo) +
    # filtre les bots/scrapers tôt.
    OnboardingStep.SELFIE_VERIFICATION,
    OnboardingStep.BASIC_INFO,
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

# Poids pour le score de complétion (§13). Total = 1.0.
# Le score mesure "à quel point ton profil est riche pour les autres",
# pas l'état d'onboarding. SELFIE et SEARCH_AREA sont des gates côté
# accès feed — gardés à 0 ici pour ne pas pénaliser le score.
# PROMPTS est calculé en palier (3% par prompt rempli, plafonné 9%) —
# cf. compute_completeness, le poids ci-dessous est l'enveloppe max.
STEP_COMPLETENESS_WEIGHT: dict[str, float] = {
    OnboardingStep.PHOTOS.value: 0.25,
    OnboardingStep.BIO.value: 0.15,
    OnboardingStep.INTENTION.value: 0.10,
    OnboardingStep.SECTOR.value: 0.10,
    OnboardingStep.QUARTIERS.value: 0.10,
    OnboardingStep.SPOTS.value: 0.10,
    OnboardingStep.PROMPTS.value: 0.09,   # palier 3% × N prompts
    OnboardingStep.TAGS.value: 0.06,
    OnboardingStep.LANGUAGES.value: 0.05,
    # Gates (présence requise pour le feed mais pas dans le %)
    OnboardingStep.BASIC_INFO.value: 0.0,
    OnboardingStep.SELFIE_VERIFICATION.value: 0.0,
    OnboardingStep.SEARCH_AREA.value: 0.0,
    OnboardingStep.CITY_SELECTION.value: 0.0,
}


def _photos_count(photos: list["Photo"] | None) -> int:
    return len(photos) if photos else 0


def _step_index(step: OnboardingStep) -> int:
    """Index of a step in ONBOARDING_FLOW. -1 si le step n'est plus
    dans le flow (legacy : sector, prompts, spots, bio, etc.)."""
    try:
        return ONBOARDING_FLOW.index(step)
    except ValueError:
        return -1


def _is_step_passed(step: OnboardingStep, user: "User") -> bool:
    """True if user.onboarding_step is strictly after this step in the flow.

    Si user.onboarding_step est un step legacy (hors flow), on retombe
    sur l'inspection du profil — `is_step_done` regardera l'état réel
    (photos count, city_id, etc.) sans s'appuyer sur la machine à états.
    """
    try:
        current = OnboardingStep(user.onboarding_step)
    except ValueError:
        return False
    current_idx = _step_index(current)
    target_idx = _step_index(step)
    if current_idx < 0 or target_idx < 0:
        return False
    return current_idx > target_idx


def is_step_done(
    step: OnboardingStep,
    user: "User",
    profile: "Profile | None",
) -> bool:
    """
    True si l'étape est satisfaite — basé UNIQUEMENT sur l'état réel
    (is_selfie_verified, profile.display_name, photos count, etc.), pas
    sur la valeur stockée `users.onboarding_step`.

    On ne court-circuite plus via `_is_step_passed` parce qu'un swap
    d'ordre dans `ONBOARDING_FLOW` rend la valeur stockée incohérente
    pour les comptes pré-existants. L'inspection de l'état réel est
    auto-correctrice : peu importe ce qui s'est passé avant, si la
    feature n'est pas remplie, l'étape n'est pas done.
    """
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
    if step is OnboardingStep.LANGUAGES:
        return profile is not None and bool(profile.languages)
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
    pondérée (poids > 0). Pour PROMPTS, on retourne aussi `count` et
    `partial_score` car le calcul est en palier (3% par prompt rempli).
    """
    score = 0.0
    breakdown: list[dict] = []
    for step_name, weight in STEP_COMPLETENESS_WEIGHT.items():
        if weight <= 0:
            continue
        step = OnboardingStep(step_name)
        # Cas spécial : prompts en palier 3% × N (max 3 → max 9%).
        # Évite que l'user spam 1 prompt vide et touche tout le quota.
        if step is OnboardingStep.PROMPTS:
            count = (
                len(profile.prompts) if profile and profile.prompts else 0
            )
            count = min(count, 3)
            partial = round(count * 0.03, 4)
            score += partial
            breakdown.append({
                "step": step_name,
                "weight": weight,
                "achieved": count >= 3,
                "count": count,
                "partial_score": partial,
            })
            continue
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
