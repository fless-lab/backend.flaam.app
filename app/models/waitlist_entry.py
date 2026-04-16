from __future__ import annotations

"""
Waitlist genrée (MàJ 7). Les femmes sont activées immédiatement
(`status="activated"`, `position=0`). Les hommes passent par la file
avec un numéro de position. Une Celery task "release_batch" libère
par lots quand le ratio femmes > 40 %.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class WaitlistEntry(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "waitlist_entries"

    city_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cities.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    # Dérivé de Profile.gender : "woman" → "female", "man" → "male",
    # "non_binary" → "other". Stocké en clair côté waitlist pour
    # permettre les filtres ratio sans jointure.
    gender: Mapped[str] = mapped_column(String(10), nullable=False)

    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # 0 pour les femmes (accès immédiat). Sinon 1..N.

    status: Mapped[str] = mapped_column(String(20), default="waiting")
    # "waiting" | "invited" | "activated" | "expired"

    invited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    invite_code_used: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )

    __table_args__ = (
        Index("ix_waitlist_city_status", "city_id", "status"),
        Index("ix_waitlist_city_gender", "city_id", "gender"),
        Index("ix_waitlist_city_position", "city_id", "position"),
    )
