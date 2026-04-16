from __future__ import annotations

"""
Rate limiter générique (spec §15).

Sliding window via Redis ZSET : chaque requête ajoute un timestamp
dans une sorted set, ZREMRANGEBYSCORE purge les vieux, ZCARD compte.

Utilisation (dependency FastAPI) :

    dep = rate_limit(max_requests=5, window_seconds=3600)
    @router.post("/safety/report", dependencies=[Depends(dep)])
    async def report(...): ...

Scope :
- "user" (défaut) : per-user-id, exige Authorization (get_current_user)
- "ip" : per-IP, pour endpoints publics non-auth

429 retourne :
- detail "rate_limited"
- header Retry-After (secondes avant la prochaine fenêtre)
- headers X-RateLimit-Limit / X-RateLimit-Remaining / X-RateLimit-Reset

Le rate limiter OTP (phone-hash-scoped, avant auth) coexiste dans
auth_service — scope différent, pas de fusion.
"""

import time
from typing import Callable, Literal

import redis.asyncio as aioredis
from fastapi import Depends, Request, status

from app.core.dependencies import get_current_user, get_redis
from app.core.exceptions import AppException
from app.models.user import User


Scope = Literal["user", "ip"]


def rate_limit(
    max_requests: int,
    window_seconds: int,
    *,
    scope: Scope = "user",
    name: str | None = None,
) -> Callable:
    """
    Factory de dependency.

    Utilise `name` pour isoler plusieurs limiteurs sur le même endpoint
    (par défaut : path de la requête).
    """
    if max_requests < 1:
        raise ValueError("max_requests must be >= 1")
    if window_seconds < 1:
        raise ValueError("window_seconds must be >= 1")

    if scope == "user":
        async def _dep_user(
            request: Request,
            user: User = Depends(get_current_user),
            redis: aioredis.Redis = Depends(get_redis),
        ) -> None:
            key_name = name or request.url.path
            key = f"ratelimit:{key_name}:user:{user.id}"
            await _check_window(
                redis=redis,
                key=key,
                max_requests=max_requests,
                window_seconds=window_seconds,
            )

        return _dep_user

    async def _dep_ip(
        request: Request,
        redis: aioredis.Redis = Depends(get_redis),
    ) -> None:
        client = request.client
        ip = client.host if client else "unknown"
        key_name = name or request.url.path
        key = f"ratelimit:{key_name}:ip:{ip}"
        await _check_window(
            redis=redis,
            key=key,
            max_requests=max_requests,
            window_seconds=window_seconds,
        )

    return _dep_ip


async def _check_window(
    *,
    redis: aioredis.Redis,
    key: str,
    max_requests: int,
    window_seconds: int,
) -> None:
    now = time.time()
    window_start = now - window_seconds

    pipe = redis.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)
    # Membre unique : timestamp + id monotone pour éviter les collisions.
    pipe.zadd(key, {f"{now}:{time.perf_counter_ns()}": now})
    pipe.zcard(key)
    pipe.expire(key, window_seconds + 1)
    _, _, count, _ = await pipe.execute()

    if int(count) > max_requests:
        # Retry-After = temps avant que le plus ancien sorte de la fenêtre.
        oldest = await redis.zrange(key, 0, 0, withscores=True)
        if oldest:
            _, oldest_ts = oldest[0]
            retry_after = max(1, int(oldest_ts + window_seconds - now))
        else:
            retry_after = window_seconds

        reset_at = int(now + window_seconds)
        exc = AppException(
            status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited"
        )
        exc.headers = {  # type: ignore[attr-defined]
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(max_requests),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(reset_at),
        }
        raise exc


__all__ = ["rate_limit"]
