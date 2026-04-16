from __future__ import annotations

"""
Poids adaptatifs entre les couches L2/L3/L4 selon l'ancienneté du compte.

Spec MàJ 5 — Philosophie :
- 0-30j    : geo=0.55, lifestyle=0.35, behavior=0.10  (pas de data comportementale)
- 30-90j   : geo=0.40, lifestyle=0.30, behavior=0.30  (équilibre)
- 90j+     : geo=0.30, lifestyle=0.25, behavior=0.45  (algo s'adapte à l'utilisateur)

Le total n'a pas besoin de faire exactement 1.0 : la pondération est
appliquée comme facteur sur les scores, pas comme moyenne pondérée stricte.
Les valeurs sont lues via config_service (Redis → DB → MATCHING_DEFAULTS).
"""

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.config_service import get_configs


async def get_adaptive_weights(
    account_age_days: int,
    redis_client: aioredis.Redis,
    db_session: AsyncSession,
) -> tuple[float, float, float]:
    """
    Retourne (geo_weight, lifestyle_weight, behavior_weight) pour un user
    dont le compte a `account_age_days` jours.
    """
    if account_age_days < 30:
        keys = ("weight_geo_0_30", "weight_lifestyle_0_30", "weight_behavior_0_30")
    elif account_age_days < 90:
        keys = ("weight_geo_30_90", "weight_lifestyle_30_90", "weight_behavior_30_90")
    else:
        keys = ("weight_geo_90_plus", "weight_lifestyle_90_plus", "weight_behavior_90_plus")

    cfg = await get_configs(keys, redis_client, db_session)
    return cfg[keys[0]], cfg[keys[1]], cfg[keys[2]]


__all__ = ["get_adaptive_weights"]
