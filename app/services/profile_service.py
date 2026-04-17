from __future__ import annotations

"""
Profile service — §5.2, §13.

Responsable de :
- get_my_profile / get_other_profile
- update_profile (création lazy du Profile au premier update)
- calculate_completeness (via app.core.onboarding)
- toggle_visibility
- get_onboarding_state / skip_onboarding_step
"""

from datetime import date
from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import FlaamError
from app.core.exceptions import AppException
from app.core.onboarding import (
    ONBOARDING_FLOW,
    SKIPPABLE_STEPS,
    OnboardingStep,
    advance_onboarding,
    compute_completeness,
    is_step_done,
)
from app.models.city import City
from app.models.profile import Profile
from app.models.user import User


# ── Helpers ──────────────────────────────────────────────────────────

def _age(birth_date: date) -> int:
    today = date.today()
    return today.year - birth_date.year - (
        (today.month, today.day) < (birth_date.month, birth_date.day)
    )


def _profile_to_my_dict(user: User, profile: Profile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "user_id": user.id,
        "display_name": profile.display_name,
        "age": _age(profile.birth_date),
        "birth_date": profile.birth_date,
        "gender": profile.gender,
        "seeking_gender": profile.seeking_gender,
        "intention": profile.intention,
        "sector": profile.sector,
        "rhythm": profile.rhythm,
        "prompts": profile.prompts or [],
        "tags": profile.tags or [],
        "languages": profile.languages or [],
        "seeking_age_min": profile.seeking_age_min,
        "seeking_age_max": profile.seeking_age_max,
        "photos": [
            {
                "id": p.id,
                "original_url": p.original_url,
                "thumbnail_url": p.thumbnail_url,
                "medium_url": p.medium_url,
                "display_order": p.display_order,
                "moderation_status": p.moderation_status,
                "width": p.width,
                "height": p.height,
                "file_size_bytes": p.file_size_bytes,
                "is_verified_selfie": p.is_verified_selfie,
                "dominant_color": p.dominant_color,
            }
            for p in (user.photos or [])
        ],
        "profile_completeness": profile.profile_completeness,
        "is_selfie_verified": user.is_selfie_verified,
        "is_id_verified": user.is_id_verified,
        "is_visible": user.is_visible,
        "city_id": user.city_id,
        "onboarding_step": user.onboarding_step,
        "updated_at": profile.updated_at,
    }


def _profile_to_public_dict(user: User, profile: Profile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "user_id": user.id,
        "display_name": profile.display_name,
        "age": _age(profile.birth_date),
        "gender": profile.gender,
        "intention": profile.intention,
        "sector": profile.sector,
        "rhythm": profile.rhythm,
        "prompts": profile.prompts or [],
        "tags": profile.tags or [],
        "languages": profile.languages or [],
        "photos": [
            {
                "id": p.id,
                "original_url": p.original_url,
                "thumbnail_url": p.thumbnail_url,
                "medium_url": p.medium_url,
                "display_order": p.display_order,
                "moderation_status": p.moderation_status,
                "width": p.width,
                "height": p.height,
                "file_size_bytes": p.file_size_bytes,
                "is_verified_selfie": p.is_verified_selfie,
                "dominant_color": p.dominant_color,
            }
            for p in (user.photos or [])
            if p.moderation_status != "rejected"
        ],
        "is_selfie_verified": user.is_selfie_verified,
    }


# ── Read ─────────────────────────────────────────────────────────────

async def get_my_profile(user: User, db: AsyncSession) -> dict:
    if user.profile is None:
        raise AppException(
            status.HTTP_404_NOT_FOUND,
            "profile_not_created",
        )
    return _profile_to_my_dict(user, user.profile)


async def get_other_profile(
    target_user_id, db: AsyncSession, lang: str = "fr"
) -> dict:
    result = await db.execute(
        select(User).where(
            User.id == target_user_id,
            User.is_active.is_(True),
            User.is_visible.is_(True),
            User.is_banned.is_(False),
            User.is_deleted.is_(False),
        )
    )
    target = result.scalar_one_or_none()
    if target is None or target.profile is None:
        raise FlaamError("profile_not_found", 404, lang)
    return _profile_to_public_dict(target, target.profile)


# ── Update ───────────────────────────────────────────────────────────

_REQUIRED_FOR_CREATE = (
    "display_name",
    "birth_date",
    "gender",
    "seeking_gender",
    "intention",
    "sector",
)


async def update_profile(
    user: User, body: dict, db: AsyncSession, lang: str = "fr"
) -> dict:
    """
    Met à jour le profil. Si aucun Profile n'existe, on en crée un (à
    l'étape BASIC_INFO de l'onboarding). Dans ce cas, les champs
    obligatoires doivent tous être présents.

    Les listes (`prompts`, `tags`, `languages`) remplacent les valeurs
    existantes quand elles sont fournies (pas de merge partiel).
    """
    data = {k: v for k, v in body.items() if v is not None}

    # ── city_id → User (pas Profile) ──
    city_id = data.pop("city_id", None)
    if city_id is not None:
        city = await db.get(City, city_id)
        if not city:
            raise FlaamError("city_not_found", 404, lang)
        if city.phase not in ("launch", "growth", "stable"):
            raise FlaamError("city_not_available", 400, lang)
        user.city_id = city_id

    # Validation range seeking_age si les deux sont fournis
    min_age = data.get("seeking_age_min")
    max_age = data.get("seeking_age_max")
    if min_age is not None and max_age is not None and min_age > max_age:
        raise AppException(
            status.HTTP_400_BAD_REQUEST,
            "seeking_age_min must be <= seeking_age_max",
        )

    # Sérialiser prompts (list[PromptEntry] → list[dict])
    if "prompts" in data:
        data["prompts"] = [
            p.model_dump() if hasattr(p, "model_dump") else dict(p)
            for p in data["prompts"]
        ]

    profile = user.profile
    if profile is None:
        # Si data contient des champs Profile → on crée le Profile,
        # sinon on skip (ex: seul city_id a été envoyé).
        if data:
            missing = [f for f in _REQUIRED_FOR_CREATE if f not in data]
            if missing:
                raise AppException(
                    status.HTTP_400_BAD_REQUEST,
                    f"missing_required_fields:{','.join(missing)}",
                )
            profile = Profile(user_id=user.id, **data)
            db.add(profile)
            user.profile = profile
    else:
        # Le genre est verrouillé après l'onboarding (principe produit
        # sécurité §CLAUDE.md). Seul un admin peut le modifier via
        # PATCH /admin/users/{id}/gender, ce qui invalide le selfie.
        if "gender" in data and data["gender"] != profile.gender:
            raise FlaamError("gender_not_modifiable", 400, lang)
        for field, value in data.items():
            setattr(profile, field, value)

    # Recompute completeness après l'update (la relation `photos` a déjà
    # été chargée par get_current_user via lazy="selectin")
    if profile is not None:
        score, _ = compute_completeness(user, profile)
        profile.profile_completeness = score

    # Avance éventuelle de l'onboarding (si on vient de valider l'étape
    # courante)
    advance_onboarding(user)

    await db.commit()
    if profile is not None:
        await db.refresh(profile)
        return _profile_to_my_dict(user, profile)
    # Pas de Profile encore (ex: seul city_id envoyé pendant l'onboarding).
    # On retourne un dict minimal — la route utilisera response_model=None.
    return {
        "city_id": str(user.city_id) if user.city_id else None,
        "onboarding_step": user.onboarding_step,
    }


# ── Patch (onboarding partiel) ────────────────────────────────────────

async def patch_profile(
    user: User, body: dict, db: AsyncSession, lang: str = "fr"
) -> dict:
    """
    Mise à jour partielle du profil pour l'onboarding step-by-step.
    Contrairement à update_profile (PUT), ne requiert PAS tous les champs
    obligatoires pour créer le Profile — intention et sector sont nullable.
    """
    data = {k: v for k, v in body.items() if v is not None}

    # ── city_id → User (pas Profile) ──
    city_id = data.pop("city_id", None)
    if city_id is not None:
        city = await db.get(City, city_id)
        if not city:
            raise FlaamError("city_not_found", 404, lang)
        if city.phase not in ("launch", "growth", "stable"):
            raise FlaamError("city_not_available", 400, lang)
        user.city_id = city_id

    # Validation range seeking_age
    min_age = data.get("seeking_age_min")
    max_age = data.get("seeking_age_max")
    if min_age is not None and max_age is not None and min_age > max_age:
        raise AppException(
            status.HTTP_400_BAD_REQUEST,
            "seeking_age_min must be <= seeking_age_max",
        )

    # Sérialiser prompts
    if "prompts" in data:
        data["prompts"] = [
            p.model_dump() if hasattr(p, "model_dump") else dict(p)
            for p in data["prompts"]
        ]

    profile = user.profile
    if profile is None:
        if data:
            profile = Profile(user_id=user.id, **data)
            db.add(profile)
            user.profile = profile
    else:
        if "gender" in data and data["gender"] != profile.gender:
            raise FlaamError("gender_not_modifiable", 400, lang)
        for field, value in data.items():
            setattr(profile, field, value)

    if profile is not None:
        score, _ = compute_completeness(user, profile)
        profile.profile_completeness = score

    advance_onboarding(user)

    await db.commit()
    if profile is not None:
        await db.refresh(profile)
        return _profile_to_my_dict(user, profile)
    return {
        "city_id": str(user.city_id) if user.city_id else None,
        "onboarding_step": user.onboarding_step,
    }


# ── Completeness ─────────────────────────────────────────────────────

async def calculate_completeness(user: User, db: AsyncSession) -> dict:
    """
    Retourne le score mis en cache (`Profile.profile_completeness`).
    Le score est recalculé et persisté à chaque `PUT /profiles/me` ou
    ajout/suppression de photo — le GET se contente donc de le servir.

    Le breakdown est reconstruit à la volée (pur calcul Python, aucune
    requête SQL supplémentaire grâce au selectin-load de `get_current_user`).
    """
    cached_score = user.profile.profile_completeness if user.profile else 0.0
    _, breakdown = compute_completeness(user, user.profile)
    return {"score": round(cached_score, 4), "breakdown": breakdown}


# ── Visibility (mode pause) ──────────────────────────────────────────

async def toggle_visibility(
    user: User, is_visible: bool, db: AsyncSession
) -> dict:
    user.is_visible = is_visible
    await db.commit()
    return {"is_visible": user.is_visible}


# ── Onboarding ───────────────────────────────────────────────────────

async def get_onboarding_state(user: User, db: AsyncSession) -> dict:
    profile = user.profile
    steps: list[dict] = []
    completed_count = 0
    current: str | None = None

    for step in ONBOARDING_FLOW:
        if step is OnboardingStep.COMPLETED:
            continue
        done = is_step_done(step, user, profile)
        skippable = step in SKIPPABLE_STEPS
        state = {
            "step": step.value,
            "status": "completed" if done else ("pending"),
            "skippable": skippable,
        }
        if done:
            completed_count += 1
        elif current is None:
            state["status"] = "in_progress"
            state["detail"] = _step_detail(step, user)
            current = step.value
        steps.append(state)

    if current is None:
        current = OnboardingStep.COMPLETED.value
        # Marquer le flow comme fini
        if user.onboarding_step != current:
            user.onboarding_step = current
            await db.commit()

    total = len([s for s in ONBOARDING_FLOW if s is not OnboardingStep.COMPLETED])
    progress = int((completed_count / total) * 100) if total else 0

    score, _ = compute_completeness(user, profile)
    return {
        "current_step": current,
        "steps": steps,
        "progress_percent": progress,
        "profile_completeness": round(score, 4),
    }


def _step_detail(step: OnboardingStep, user: User) -> dict | None:
    if step is OnboardingStep.PHOTOS:
        return {"count": len(user.photos or []), "min": 3}
    return None


async def skip_onboarding_step(
    user: User, step_name: str, db: AsyncSession
) -> dict:
    try:
        step = OnboardingStep(step_name)
    except ValueError:
        raise AppException(status.HTTP_400_BAD_REQUEST, "unknown_step")

    if step not in SKIPPABLE_STEPS:
        raise AppException(
            status.HTTP_400_BAD_REQUEST,
            f"step_not_skippable:{step.value}",
        )

    # Avance la machine à la prochaine étape non satisfaite après celle-ci
    idx = ONBOARDING_FLOW.index(step)
    target = OnboardingStep.COMPLETED
    for candidate in ONBOARDING_FLOW[idx + 1 :]:
        if candidate is OnboardingStep.COMPLETED:
            target = candidate
            break
        if not is_step_done(candidate, user, user.profile):
            target = candidate
            break
    user.onboarding_step = target.value
    await db.commit()

    warning = None
    if step is OnboardingStep.PROMPTS:
        warning = (
            "Your profile will be less visible without prompts. "
            "You can add them later."
        )
    return {
        "skipped": step.value,
        "next_step": target.value,
        "warning": warning,
    }


__all__ = [
    "get_my_profile",
    "get_other_profile",
    "update_profile",
    "patch_profile",
    "calculate_completeness",
    "toggle_visibility",
    "get_onboarding_state",
    "skip_onboarding_step",
]
