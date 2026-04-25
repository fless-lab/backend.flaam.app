from __future__ import annotations

"""
Flame service — gestion du QR token rotatif pour insta-match IRL.

Pattern d'usage :
  1. Mobile appelle GET /flame/me → renvoie {qr_token, expires_at,
     scan_enabled, scans_received_max, scans_sent_today,
     scans_received_today}.
  2. Mobile encode `qr_token` dans un QR code grand format.
  3. Quelqu'un scanne ce QR → POST /matches/instant {scanned_qr_token,
     scanner_lat, scanner_lng, event_id?}.
  4. Backend lookup le token, vérifie sécurité, crée un Match direct.

Le token tourne toutes les 24h pour empêcher les copies WhatsApp/
screenshot durables d'être réutilisées plusieurs jours plus tard.
"""

import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.user_flame import UserFlame


TOKEN_VALIDITY_HOURS = 24
TOKEN_LENGTH = 32  # urlsafe → 43 chars (2x32 base64) — fit dans QR petite densité


def _generate_token() -> str:
    """Token cryptographique 32 octets, URL-safe."""
    return secrets.token_urlsafe(TOKEN_LENGTH)


def _is_expired(rotated_at: datetime) -> bool:
    if rotated_at.tzinfo is None:
        rotated_at = rotated_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - rotated_at > timedelta(
        hours=TOKEN_VALIDITY_HOURS,
    )


async def get_or_create_flame(user: User, db: AsyncSession) -> UserFlame:
    """
    Récupère le UserFlame du user, en (re)génère un si absent ou expiré.
    Idempotent — safe à appeler à chaque GET /flame/me.
    """
    result = await db.execute(
        select(UserFlame).where(UserFlame.user_id == user.id),
    )
    flame = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if flame is None:
        flame = UserFlame(
            user_id=user.id,
            qr_token=_generate_token(),
            rotated_at=now,
        )
        db.add(flame)
        await db.commit()
        await db.refresh(flame)
        return flame

    if _is_expired(flame.rotated_at):
        flame.qr_token = _generate_token()
        flame.rotated_at = now
        await db.commit()
        await db.refresh(flame)

    return flame


async def find_user_by_token(token: str, db: AsyncSession) -> User | None:
    """
    Lookup le user qui possède ce token. Renvoie None si :
    - token absent en DB
    - token expiré (>24h ago) — refusé même s'il existe encore
    """
    result = await db.execute(
        select(UserFlame, User)
        .join(User, User.id == UserFlame.user_id)
        .where(UserFlame.qr_token == token),
    )
    row = result.first()
    if row is None:
        return None
    flame, user = row
    if _is_expired(flame.rotated_at):
        return None
    return user
