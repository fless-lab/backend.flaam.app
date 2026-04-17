from __future__ import annotations

"""Tests cache read-through (§25, Session 12)."""

import asyncio

import pytest

from app.core.cache import (
    cache_get,
    cache_invalidate,
    cache_invalidate_pattern,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_cache_get_miss_calls_fallback(redis_client):
    """Premier appel → fallback exécuté, résultat caché."""
    calls = {"n": 0}

    async def fallback():
        calls["n"] += 1
        return {"value": 42}

    result = await cache_get("test:miss", fallback, 60, redis_client)
    assert result == {"value": 42}
    assert calls["n"] == 1
    # La clé est bien en cache
    assert await redis_client.get("test:miss") is not None


async def test_cache_get_hit_skips_fallback(redis_client):
    """Deuxième appel sur même clé → fallback NON appelé."""
    calls = {"n": 0}

    async def fallback():
        calls["n"] += 1
        return {"value": "cached"}

    await cache_get("test:hit", fallback, 60, redis_client)
    await cache_get("test:hit", fallback, 60, redis_client)
    assert calls["n"] == 1


async def test_cache_invalidate_clears_key(redis_client):
    """Après cache_invalidate, la prochaine lecture rappelle le fallback."""
    calls = {"n": 0}

    async def fallback():
        calls["n"] += 1
        return {"value": calls["n"]}

    await cache_get("test:invalidate", fallback, 60, redis_client)
    await cache_invalidate("test:invalidate", redis_client)
    result = await cache_get("test:invalidate", fallback, 60, redis_client)
    assert calls["n"] == 2
    assert result == {"value": 2}


async def test_cache_invalidate_pattern_clears_matching(redis_client):
    """SCAN + DEL supprime toutes les clés qui matchent le pattern."""
    await redis_client.set("feed:u1", "a")
    await redis_client.set("feed:u2", "b")
    await redis_client.set("feed:u3", "c")
    await redis_client.set("other:u1", "z")

    deleted = await cache_invalidate_pattern("feed:*", redis_client)
    assert deleted == 3
    assert await redis_client.get("feed:u1") is None
    assert await redis_client.get("feed:u2") is None
    assert await redis_client.get("feed:u3") is None
    # Les clés hors pattern restent
    assert await redis_client.get("other:u1") == "z"


async def test_stampede_prevention(redis_client):
    """
    Deux appels simultanés sur une clé absente : le fallback lent est
    appelé une seule fois (le second attend le lock puis lit le cache).
    """
    calls = {"n": 0}

    async def slow_fallback():
        calls["n"] += 1
        # Plus long que le wait du lock pour valider l'attente
        await asyncio.sleep(0.1)
        return {"value": "stampede"}

    r1, r2 = await asyncio.gather(
        cache_get("test:stampede", slow_fallback, 60, redis_client),
        cache_get("test:stampede", slow_fallback, 60, redis_client),
    )
    assert r1 == {"value": "stampede"}
    assert r2 == {"value": "stampede"}
    assert calls["n"] == 1
