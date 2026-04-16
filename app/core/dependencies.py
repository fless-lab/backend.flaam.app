from __future__ import annotations

"""
Dépendances FastAPI partagées : DB, Redis, utilisateur courant.
"""

from typing import AsyncIterator
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.security import JWTError, decode_token
from app.db.redis import redis_pool
from app.db.session import async_session
from app.models.user import User


async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session


def get_redis() -> aioredis.Redis:
    return redis_pool.client


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    return authorization.split(" ", 1)[1].strip()


async def get_current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = _extract_bearer_token(authorization)
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
