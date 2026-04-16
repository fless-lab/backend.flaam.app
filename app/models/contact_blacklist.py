from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class ContactBlacklist(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "contact_blacklists"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    phone_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (
        Index("uq_contact_blacklist", "user_id", "phone_hash", unique=True),
        Index("ix_contact_blacklist_phone", "phone_hash"),
    )
