from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class NotificationPreference(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    new_match: Mapped[bool] = mapped_column(Boolean, default=True)
    new_message: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_feed: Mapped[bool] = mapped_column(Boolean, default=True)
    events: Mapped[bool] = mapped_column(Boolean, default=True)
    date_reminder: Mapped[bool] = mapped_column(Boolean, default=True)
    weekly_digest: Mapped[bool] = mapped_column(Boolean, default=True)
    # Feature C (Session 9) : reply reminders. Activé par défaut ;
    # l'utilisateur peut le désactiver via PUT /notifications/preferences.
    reply_reminders: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )

    daily_feed_hour: Mapped[int] = mapped_column(Integer, default=9)
    quiet_start_hour: Mapped[int] = mapped_column(Integer, default=23)
    quiet_end_hour: Mapped[int] = mapped_column(Integer, default=7)

    user = relationship("User", back_populates="notification_prefs")
