from __future__ import annotations

"""
Dépendances de routes partagées (§5.20 Admin).

Ce module ré-exporte les dépendances bas-niveau de `core/dependencies.py`
et ajoute `get_admin_user` — vérification d'un flag `User.is_admin`.

Le flag n'est **jamais** modifiable via un endpoint utilisateur : la
promotion admin se fait manuellement en base (psql ou script de seed).
"""

from datetime import datetime, timezone

from fastapi import Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db, get_redis
from app.core.exceptions import AppException
from app.core.security import (
    compute_pin_lock_until,
    verify_pin as _verify_pin_helper,
)
from app.models.user import User


async def get_admin_user(
    user: User = Depends(get_current_user),
) -> User:
    if not user.is_admin:
        raise AppException(status.HTTP_403_FORBIDDEN, "admin_required")
    return user


async def require_pin(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    x_pin_verification: str | None = Header(default=None),
) -> User:
    """
    Gate PIN pour les opérations sensibles (#214).

    - Si l'user n'a PAS de PIN configuré → laisse passer (gate inactif).
    - Si l'user a un PIN :
      * Pas de header X-Pin-Verification → 412 PRECONDITION_FAILED
        `pin_required` (le mobile sait qu'il doit prompt l'user).
      * Header présent mais PIN faux → 401 `invalid_pin` + incrément
        compteur d'échecs (anti-bruteforce, cf. mfa_locked).
      * Si lock actif → 429 `mfa_locked:{remaining}`.

    NB : le PIN voyage en header pour ne pas polluer les bodies métier.
    """
    if not user.mfa_enabled or not user.mfa_pin_hash:
        return user

    # Lock anti-bruteforce
    if user.mfa_locked_until is not None:
        locked = user.mfa_locked_until
        if locked.tzinfo is None:
            locked = locked.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if locked > now:
            remaining = int((locked - now).total_seconds())
            raise AppException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"mfa_locked:{remaining}",
            )

    if not x_pin_verification:
        raise AppException(
            status.HTTP_412_PRECONDITION_FAILED, "pin_required",
        )

    if not _verify_pin_helper(x_pin_verification, user.mfa_pin_hash):
        user.mfa_failed_attempts = (user.mfa_failed_attempts or 0) + 1
        user.mfa_locked_until = compute_pin_lock_until(user.mfa_failed_attempts)
        await db.commit()
        raise AppException(status.HTTP_401_UNAUTHORIZED, "invalid_pin")

    # Reset compteur si on était en cours d'erreurs
    if user.mfa_failed_attempts or user.mfa_locked_until:
        user.mfa_failed_attempts = 0
        user.mfa_locked_until = None
        await db.commit()
    return user


async def require_email_verified(
    user: User = Depends(get_current_user),
) -> User:
    """
    Gate email vérifié pour les ops critiques (delete account, change
    phone, reset PIN). 412 PRECONDITION_FAILED `email_required` si
    pas d'email lié ou pas encore vérifié.
    """
    if not user.email or not user.is_email_verified:
        raise AppException(
            status.HTTP_412_PRECONDITION_FAILED, "email_required",
        )
    return user


__all__ = [
    "get_db",
    "get_redis",
    "get_current_user",
    "get_admin_user",
    "require_pin",
    "require_email_verified",
]
