from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class UserSpot(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "user_spots"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    spot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spots.id", ondelete="CASCADE"),
        nullable=False,
    )

    checkin_count: Mapped[int] = mapped_column(Integer, default=0)
    last_checkin_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_checkin_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    fidelity_level: Mapped[str] = mapped_column(String(20), default="declared")
    fidelity_score: Mapped[float] = mapped_column(Float, default=0.5)
    is_visible: Mapped[bool] = mapped_column(default=True)
    # Gel doux premium → False désactive le spot du matching sans le
    # supprimer (spec §business-model).
    is_active_in_matching: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )

    user = relationship("User", back_populates="user_spots")
    spot = relationship("Spot", lazy="selectin")

    __table_args__ = (
        Index("ix_user_spots_user", "user_id"),
        Index("ix_user_spots_spot", "spot_id"),
        Index("uq_user_spot", "user_id", "spot_id", unique=True),
    )
