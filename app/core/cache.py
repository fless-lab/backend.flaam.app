from __future__ import annotations

"""
Cache Redis read-through utilitaire (§25).

Pattern standard pour tout cache applicatif :
- cache_get(key, fallback_fn, ttl, redis) : lit le cache, sinon génère
  via fallback_fn et écrit. Anti-stampede via lock Redis (NX EX 30s).
- cache_invalidate(key, redis) : DEL simple.
- cache_invalidate_pattern(pattern, redis) : SCAN + DEL par batch.

Pour les appels parallèles simultanés sur une même clé manquante :
un seul process acquiert le lock et régénère. Les autres attendent 1 s
puis retentent la lecture cache. Si le lock a expiré (edge case) ils
régénèrent aussi, garantissant qu'on ne bloque jamais une requête.

Les services qui ont leur propre logique (feed_service avec fallback
FeedCache DB, config_service avec batch MGET) continuent de l'utiliser
tels quels — cache_get est pour les nouveaux caches « simples ».
"""

import asyncio
import json

import redis.asyncio as aioredis
import structlog

log = structlog.get_logger()

_LOCK_TTL_SECONDS = 30
_LOCK_WAIT_SECONDS = 1.0


async def cache_get(
    key: str,
    fallback_fn,
    ttl: int,
    redis: aioredis.Redis,
):
    """
    Read-through avec stampede prevention.

    fallback_fn est une coroutine 0-arg qui retourne la valeur à cacher.
    La valeur doit être JSON-sérialisable (default=str pour datetime).
    """
    cached = await redis.get(key)
    if cached is not None:
        try:
            return json.loads(cached)
        except (TypeError, json.JSONDecodeError):
            pass  # valeur corrompue → on régénère

    lock_key = f"lock:{key}"
    acquired = await redis.set(lock_key, "1", nx=True, ex=_LOCK_TTL_SECONDS)

    if not acquired:
        # Un autre worker régénère. On attend puis on retente.
        await asyncio.sleep(_LOCK_WAIT_SECONDS)
        cached = await redis.get(key)
        if cached is not None:
            try:
                return json.loads(cached)
            except (TypeError, json.JSONDecodeError):
                pass
        # Fallback de sécurité : lock orphelin ou TTL dépassé.

    try:
        result = await fallback_fn()
        serialized = json.dumps(result, default=str)
        await redis.set(key, serialized, ex=ttl)
        return result
    finally:
        if acquired:
            await redis.delete(lock_key)


async def cache_invalidate(key: str, redis: aioredis.Redis) -> None:
    """DEL d'une clé (idempotent)."""
    await redis.delete(key)


async def cache_invalidate_pattern(
    pattern: str, redis: aioredis.Redis
) -> int:
    """
    SCAN + DEL de toutes les clés qui matchent le pattern.

    Utilise SCAN (pas KEYS) : O(N) mais non-bloquant en prod. Retourne
    le nombre de clés supprimées.
    """
    cursor = 0
    deleted = 0
    while True:
        cursor, keys = await redis.scan(cursor, match=pattern, count=100)
        if keys:
            deleted += await redis.delete(*keys)
        if cursor == 0:
            break
    return deleted


__all__ = ["cache_get", "cache_invalidate", "cache_invalidate_pattern"]
