from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class Photo(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "photos"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    original_url: Mapped[str] = mapped_column(String(500), nullable=False)
    thumbnail_url: Mapped[str] = mapped_column(String(500), nullable=False)
    medium_url: Mapped[str] = mapped_column(String(500), nullable=False)

    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    is_verified_selfie: Mapped[bool] = mapped_column(Boolean, default=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    moderation_status: Mapped[str] = mapped_column(String(20), default="pending")
    moderation_score: Mapped[float | None] = mapped_column(nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Couleur dominante en hex (#RRGGBB) — placeholder pendant le chargement
    # progressif côté mobile (§30 cache strategy).
    dominant_color: Mapped[str | None] = mapped_column(String(7), nullable=True)

    user = relationship("User", back_populates="photos")

    __table_args__ = (
        CheckConstraint(
            "display_order >= 0 AND display_order <= 5", name="ck_photo_order"
        ),
    )
