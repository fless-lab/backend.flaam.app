from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Index
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class FeedCache(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "feed_caches"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    feed_date: Mapped[date] = mapped_column(Date, nullable=False)
    profile_ids: Mapped[list] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )
    wildcard_ids: Mapped[list] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )
    new_user_ids: Mapped[list] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )

    __table_args__ = (
        Index("uq_feed_user_date", "user_id", "feed_date", unique=True),
        Index("ix_feed_date", "feed_date"),
    )
