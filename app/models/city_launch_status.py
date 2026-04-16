from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class CityLaunchStatus(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "city_launch_statuses"

    city_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cities.id"), unique=True, nullable=False
    )
    phase: Mapped[str] = mapped_column(String(20), default="teaser")

    total_registered: Mapped[int] = mapped_column(Integer, default=0)
    male_registered: Mapped[int] = mapped_column(Integer, default=0)
    female_registered: Mapped[int] = mapped_column(Integer, default=0)

    launched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    waitlist_invites_total: Mapped[int] = mapped_column(Integer, default=0)
