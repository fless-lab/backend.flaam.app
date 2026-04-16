from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class Match(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "matches"

    user_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(String(20), default="pending")
    liked_prompt_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ── Targeted like (Feature A, Session 9) ──
    # Quand flag_targeted_likes_enabled est actif, POST /feed/{id}/like
    # accepte target_type in {"profile","photo","prompt"}, target_id et
    # comment. Le comment devient l'ice-breaker si présent.
    like_target_type: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )
    like_target_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    like_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    matched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    unmatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    unmatched_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    geo_score: Mapped[float | None] = mapped_column(nullable=True)
    lifestyle_score: Mapped[float | None] = mapped_column(nullable=True)
    was_wildcard: Mapped[bool] = mapped_column(default=False)

    user_a = relationship("User", foreign_keys=[user_a_id], lazy="selectin")
    user_b = relationship("User", foreign_keys=[user_b_id], lazy="selectin")
    messages = relationship(
        "Message",
        back_populates="match",
        order_by="Message.created_at",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_matches_user_a", "user_a_id", "status"),
        Index("ix_matches_user_b", "user_b_id", "status"),
        Index(
            "ix_matches_expires",
            "expires_at",
            postgresql_where="status = 'matched' AND expires_at IS NOT NULL",
        ),
        Index("uq_match_pair", "user_a_id", "user_b_id", unique=True),
    )
