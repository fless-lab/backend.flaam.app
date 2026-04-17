from __future__ import annotations

"""
EmergencyContact (§S12.5, safety §5.11).

Contact de confiance pré-enregistré par un user. Utilisé au moment
d'activer un timer d'urgence (selection d'au plus 2 contacts parmi les
3 stockables) ou quand le bouton panique est déclenché sans timer
actif (SMS envoyé au contact `is_primary=True`).

Règles :
- Max 3 contacts stockés par user (validation service).
- Premier contact enregistré → is_primary=True automatiquement.
- À la suppression du primary, le plus ancien contact restant devient
  primary.
- Feature GRATUITE pour tous — la sécurité n'est jamais derrière un
  paywall (principe produit CLAUDE.md §Sécurité).
"""

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class EmergencyContact(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "emergency_contacts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Format E.164 attendu (+indicatif + numéro). Validé côté schema.
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    __table_args__ = (
        Index("ix_emergency_contacts_user", "user_id"),
    )
