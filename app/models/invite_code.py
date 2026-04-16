from __future__ import annotations

"""
Codes d'invitation (MàJ 7). Format `FLAAM-XXXX` (8 chars après le
préfixe). 3 codes par femme inscrite, 50 par ambassadrice. Les
hommes non-ambassadeurs n'en reçoivent pas.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class InviteCode(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "invite_codes"

    code: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )
    # Format : "FLAAM-XXXXXXXX"

    creator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    city_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cities.id"), nullable=False
    )

    type: Mapped[str] = mapped_column(
        String(20), default="standard", server_default="standard"
    )
    # "standard" | "ambassador" | "event"

    used_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )

    __table_args__ = (
        Index("ix_invite_codes_creator", "creator_id"),
        Index("ix_invite_codes_city", "city_id"),
    )
