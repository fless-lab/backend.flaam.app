from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class Block(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "blocks"

    blocker_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    blocked_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    __table_args__ = (
        Index("uq_block", "blocker_id", "blocked_id", unique=True),
        Index("ix_blocks_blocker", "blocker_id"),
        Index("ix_blocks_blocked", "blocked_id"),
    )
