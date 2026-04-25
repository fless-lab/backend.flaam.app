from __future__ import annotations

"""
EventCheckin — un user a physiquement check-in à un event (GPS verified).

Distinct de EventRegistration (qui est l'inscription "je viendrai"). Un
user peut s'inscrire mais ne pas check-in (ne pas être venu) — et
réciproquement, on n'autorise pas de check-in sans inscription
préalable côté service.

Table append-only : un user peut avoir plusieurs check-ins pour le même
event (ex: il sort puis revient). On garde tout, le requêteur filtre
par fenêtre temporelle (ex: <2h pour flame/nearby).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class EventCheckin(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "event_checkins"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Coordonnées GPS au moment du check-in (pour audit + flame/nearby).
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    # GPS validé < 200m du venue ? (sinon refusé côté service).
    verified: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False,
    )
    # Override `created_at` pour les requêtes par window — index dédié.
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    __table_args__ = (
        Index("ix_event_checkins_event_at", "event_id", "at"),
        Index("ix_event_checkins_user", "user_id"),
    )
