from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class Event(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "events"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    spot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("spots.id"), nullable=False
    )
    city_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cities.id"), nullable=False
    )

    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    category: Mapped[str] = mapped_column(String(30), nullable=False)
    max_attendees: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_attendees: Mapped[int] = mapped_column(Integer, default=0)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    is_admin_created: Mapped[bool] = mapped_column(Boolean, default=False)
    is_sponsored: Mapped[bool] = mapped_column(Boolean, default=False)
    sponsor_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Lifecycle : draft → published → full → ongoing → completed | cancelled
    # (MàJ 8 Porte 3). `full` est calculé lors du register quand
    # current_attendees atteint max_attendees.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    # Slug stable pour la page web publique flaam.app/events/{slug}
    slug: Mapped[str | None] = mapped_column(
        String(120), unique=True, nullable=True, index=True
    )

    spot = relationship("Spot", lazy="selectin")
    registrations = relationship(
        "EventRegistration", back_populates="event", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_events_city_date", "city_id", "starts_at"),
        Index("ix_events_active", "is_active", "is_approved", "starts_at"),
    )
