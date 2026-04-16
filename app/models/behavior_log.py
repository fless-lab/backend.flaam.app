from __future__ import annotations

import uuid

from sqlalchemy import Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class BehaviorLog(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "behavior_logs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    extra_data: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )

    __table_args__ = (
        Index("ix_behavior_user_type", "user_id", "event_type"),
        Index("ix_behavior_created", "created_at"),
    )
