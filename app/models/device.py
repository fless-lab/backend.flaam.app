from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class Device(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "devices"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    device_fingerprint: Mapped[str] = mapped_column(String(256), nullable=False)
    platform: Mapped[str] = mapped_column(String(10), nullable=False)
    fcm_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_login_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user = relationship("User", back_populates="devices")
