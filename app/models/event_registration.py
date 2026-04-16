from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class EventRegistration(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "event_registrations"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # registered → checked_in → converted | expired  (MàJ 8 Porte 3)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="registered", server_default="registered"
    )
    # "app" = inscription depuis l'app (portes 1/2)
    # "web" = inscription depuis la page web event (porte 3)
    registered_via: Mapped[str] = mapped_column(
        String(10), nullable=False, default="app", server_default="app"
    )
    checked_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Hash SHA-256 du QR signé HMAC — clé d'idempotence du check-in.
    qr_code_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    # Tags auto-suggérés depuis la catégorie de l'event, utilisés lors
    # de la conversion ghost → app (pré-cochés dans l'onboarding).
    suggested_tags: Mapped[list | None] = mapped_column(JSON, nullable=True)

    event = relationship("Event", back_populates="registrations")

    __table_args__ = (
        Index("uq_event_registration", "event_id", "user_id", unique=True),
    )
