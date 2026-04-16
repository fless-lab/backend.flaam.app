from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin


class AccountHistory(Base, UUIDMixin):
    __tablename__ = "account_histories"

    phone_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    device_fingerprints: Mapped[list] = mapped_column(JSONB, default=list)

    total_accounts_created: Mapped[int] = mapped_column(Integer, default=1)
    total_accounts_deleted: Mapped[int] = mapped_column(Integer, default=0)
    total_bans: Mapped[int] = mapped_column(Integer, default=0)

    first_account_created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_account_created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_account_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_ban_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    last_departure_reason: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    current_restriction: Mapped[str] = mapped_column(String(30), default="none")
    restriction_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    blocked_by_hashes: Mapped[list] = mapped_column(JSONB, default=list)
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_ah_phone", "phone_hash"),
        Index("ix_ah_risk", "risk_score"),
        Index("ix_ah_devices", "device_fingerprints", postgresql_using="gin"),
    )
