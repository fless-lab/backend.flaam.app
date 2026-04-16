from __future__ import annotations

"""
Routes Config (§5.14, §27, §28). 2 endpoints.

- GET /config/version        : pas d'auth (client non-loggué peut checker)
- GET /config/feature-flags  : user-scoped
"""

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import get_current_user, get_db, get_redis
from app.models.user import User
from app.schemas.config import FeatureFlagsResponse, VersionResponse
from app.services.config_service import get_configs

router = APIRouter(prefix="/config", tags=["config"])


# Liste des feature flags exposés côté client.
# Convention : clé MatchingConfig = `flag_{name}`, valeur 1.0 ou 0.0.
# Le fallback (si absent de la DB) lit MATCHING_DEFAULTS.
PUBLIC_FEATURE_FLAGS = [
    "flag_premium_enabled",
    "flag_events_enabled",
    "flag_voice_messages_enabled",
    "flag_ice_breakers_enabled",
    "flag_targeted_likes_enabled",
    "flag_reply_reminders_enabled",
]


@router.get("/version", response_model=VersionResponse)
async def get_version() -> dict:
    s = get_settings()
    return {
        "min_version": s.app_min_version,
        "current_version": s.app_current_version,
        "force_update": s.app_force_update,
        "update_url": s.app_update_url,
    }


@router.get("/feature-flags", response_model=FeatureFlagsResponse)
async def get_feature_flags(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    values = await get_configs(PUBLIC_FEATURE_FLAGS, redis, db)
    # Convention 1.0 = activé, tout le reste = désactivé.
    flags = {k: (v >= 0.5) for k, v in values.items()}
    return {"flags": flags}


__all__ = ["router"]
