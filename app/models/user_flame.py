from __future__ import annotations

"""
UserFlame — token QR rotatif pour insta-match IRL.

Chaque user a UN UserFlame (relation 1-1). Le `qr_token` est un secret
cryptographiquement sûr (32 chars urlsafe), encodé dans le QR code que
l'user montre IRL. Quand quelqu'un scanne ce QR :
  POST /matches/instant {scanned_qr_token, scanner_lat, scanner_lng}

Le token est régénéré toutes les 24h (cf. invite_service pattern), pour
empêcher les copies WhatsApp/screenshot durables d'être réutilisées
plusieurs jours après. Si quelqu'un partage son QR par capture, le
token expire et le scan échoue.

Pas dérivable du user_id directement (pas de tableau d'index inverse) :
on stocke le token comme la valeur primary du lookup.
"""

import uuid

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin
from datetime import datetime


class UserFlame(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "user_flames"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    # Token actuellement valide. Encodé dans le QR.
    qr_token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True,
    )
    # Dernière rotation. Si > 24h ago → on régénère au prochain GET /flame/me.
    rotated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
