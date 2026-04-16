from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class Message(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "messages"

    match_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matches.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    message_type: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    media_duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meetup_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    read_at: Mapped[str | None] = mapped_column(nullable=True)

    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)

    match = relationship("Match", back_populates="messages")

    __table_args__ = (
        Index("ix_messages_match", "match_id", "created_at"),
        Index("ix_messages_sender", "sender_id"),
    )
