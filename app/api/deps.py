from __future__ import annotations

"""
Dépendances de routes partagées (§5.20 Admin).

Ce module ré-exporte les dépendances bas-niveau de `core/dependencies.py`
et ajoute `get_admin_user` — vérification d'un flag `User.is_admin`.

Le flag n'est **jamais** modifiable via un endpoint utilisateur : la
promotion admin se fait manuellement en base (psql ou script de seed).
"""

from fastapi import Depends, status

from app.core.dependencies import get_current_user, get_db, get_redis
from app.core.exceptions import AppException
from app.models.user import User


async def get_admin_user(
    user: User = Depends(get_current_user),
) -> User:
    if not user.is_admin:
        raise AppException(status.HTTP_403_FORBIDDEN, "admin_required")
    return user


__all__ = ["get_db", "get_redis", "get_current_user", "get_admin_user"]
