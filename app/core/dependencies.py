from __future__ import annotations

"""
Dépendances FastAPI partagées : DB, Redis, utilisateur courant.
"""

from typing import AsyncIterator
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.security import JWTError, decode_token
from app.db.redis import redis_pool
from app.db.session import async_session
from app.models.user import User

bearer_scheme = HTTPBearer(
    scheme_name="JWT",
    description="Access token JWT. Format : Bearer {token}",
    auto_error=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session


def get_redis() -> aioredis.Redis:
    return redis_pool.client


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise AppException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = credentials.credentials
    try:
        payload = decode_token(token)
    except JWTError:
        raise AppException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    if payload.get("type") != "access":
        raise AppException(status.HTTP_401_UNAUTHORIZED, "Wrong token type")

    try:
        user_id = UUID(payload["sub"])
    except (KeyError, ValueError):
        raise AppException(status.HTTP_401_UNAUTHORIZED, "Invalid token subject")

    user = await db.get(User, user_id)
    if (
        user is None
        or not user.is_active
        or user.is_banned
        or user.is_deleted
    ):
        raise AppException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


__all__ = ["get_db", "get_redis", "get_current_user"]
