from __future__ import annotations

"""
Config service pour les paramètres du matching engine.

Cache-through : Redis → DB → MATCHING_DEFAULTS.

- Clé Redis : matching:config:{key}, TTL 1h
- Table DB : matching_configs.value (Float)
- Fallback : app.core.constants.MATCHING_DEFAULTS

Appelé à chaque calcul de feed. La couche Redis est critique — sans elle,
on fait 1 SELECT par clé par user, soit des dizaines de requêtes DB par
batch.
"""

from typing import Iterable

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    MATCHING_DEFAULTS,
    REDIS_CONFIG_KEY,
    REDIS_CONFIG_TTL_SECONDS,
)
from app.models.matching_config import MatchingConfig


async def get_config(
    key: str,
    redis_client: aioredis.Redis,
    db_session: AsyncSession,
) -> float:
    """
    Retourne la valeur float pour une clé de config.
    Redis → DB → MATCHING_DEFAULTS.
    """
    redis_key = REDIS_CONFIG_KEY.format(key=key)
    cached = await redis_client.get(redis_key)
    if cached is not None:
        try:
            return float(cached)
        except (TypeError, ValueError):
            pass  # Valeur corrompue, on recharge depuis la DB

    row = await db_session.execute(
        select(MatchingConfig.value).where(MatchingConfig.key == key)
    )
    db_value = row.scalar_one_or_none()
    if db_value is not None:
        await redis_client.set(
            redis_key, str(db_value), ex=REDIS_CONFIG_TTL_SECONDS
        )
        return float(db_value)

    default = MATCHING_DEFAULTS.get(key)
    if default is None:
        raise KeyError(f"Unknown matching config key: {key}")
    # On ne met pas le default en Redis : l'absence en DB ne doit pas
    # être persistée dans le cache (sinon un admin qui ajoute une ligne
    # doit attendre l'expiration).
    return float(default)


async def get_configs(
    keys: Iterable[str],
    redis_client: aioredis.Redis,
    db_session: AsyncSession,
) -> dict[str, float]:
    """
    Batch de get_config pour N clés. Utile au début d'un calcul de feed
    pour charger tout le bundle lifestyle/geo/behavior d'un coup.
    """
    keys_list = list(keys)
    out: dict[str, float] = {}
    if not keys_list:
        return out

    # 1. Batch Redis MGET
    redis_keys = [REDIS_CONFIG_KEY.format(key=k) for k in keys_list]
    cached_values = await redis_client.mget(redis_keys)

    missing: list[str] = []
    for k, v in zip(keys_list, cached_values):
        if v is None:
            missing.append(k)
            continue
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            missing.append(k)

    if not missing:
        return out

    # 2. DB pour les manquantes
    rows = await db_session.execute(
        select(MatchingConfig.key, MatchingConfig.value).where(
            MatchingConfig.key.in_(missing)
        )
    )
    db_map = {k: v for k, v in rows.all()}

    # 3. Populate Redis + out dict
    pipe = redis_client.pipeline()
    for k in missing:
        if k in db_map:
            out[k] = float(db_map[k])
            pipe.set(
                REDIS_CONFIG_KEY.format(key=k),
                str(db_map[k]),
                ex=REDIS_CONFIG_TTL_SECONDS,
            )
        else:
            default = MATCHING_DEFAULTS.get(k)
            if default is None:
                raise KeyError(f"Unknown matching config key: {k}")
            out[k] = float(default)
    await pipe.execute()

    return out


async def invalidate_config(key: str, redis_client: aioredis.Redis) -> None:
    """Supprime l'entrée Redis. Appelé après un UPDATE admin."""
    await redis_client.delete(REDIS_CONFIG_KEY.format(key=key))


__all__ = ["get_config", "get_configs", "invalidate_config"]
