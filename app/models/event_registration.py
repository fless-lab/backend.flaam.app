from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index
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

    event = relationship("Event", back_populates="registrations")

    __table_args__ = (
        Index("uq_event_registration", "event_id", "user_id", unique=True),
    )
