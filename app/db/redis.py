from __future__ import annotations

import redis.asyncio as aioredis

from app.core.config import get_settings

settings = get_settings()


class RedisPool:
    def __init__(self) -> None:
        self._pool: aioredis.Redis | None = None

    async def initialize(self) -> None:
        self._pool = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
        )

    @property
    def client(self) -> aioredis.Redis:
        if self._pool is None:
            raise RuntimeError("Redis pool not initialized")
        return self._pool

    async def close(self) -> None:
        if self._pool:
            await self._pool.aclose()


redis_pool = RedisPool()
