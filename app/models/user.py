from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    phone_hash: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False, index=True
    )
    phone_country_code: Mapped[str] = mapped_column(String(5), nullable=False)

    is_phone_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_selfie_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_id_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    ban_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Soft-delete RGPD (§17). `deleted_at` set ⇒ pipeline RGPD déclenché.
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # city_id est renseigné à l'étape CITY_SELECTION de l'onboarding
    # (spec §13). À la création par OTP verify, il est null.
    city_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cities.id"), nullable=True
    )
    onboarding_step: Mapped[str] = mapped_column(
        String(30),
        default="city_selection",
        server_default="city_selection",
        nullable=False,
    )

    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_feed_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    language: Mapped[str] = mapped_column(String(5), default="fr")
    account_created_count: Mapped[int] = mapped_column(default=1)

    # ── Programme ambassadrices (MàJ 7) ──
    # Les ambassadrices reçoivent 50 codes d'invitation et bypassent la
    # waitlist pour elles-mêmes.
    is_ambassador: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    # ── Email (optionnel, encouragé pour recovery + notifications) ──
    email: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True
    )
    is_email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── MFA optionnel (PIN 6 chiffres hashé bcrypt) ──
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_pin_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # ── Recovery email (peut différer de l'email principal) ──
    recovery_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    city = relationship("City", lazy="selectin")
    profile = relationship(
        "Profile", back_populates="user", uselist=False, lazy="selectin"
    )
    photos = relationship(
        "Photo",
        back_populates="user",
        lazy="selectin",
        order_by="Photo.display_order",
    )
    devices = relationship("Device", back_populates="user", lazy="selectin")
    user_quartiers = relationship(
        "UserQuartier", back_populates="user", lazy="selectin"
    )
    user_spots = relationship("UserSpot", back_populates="user", lazy="selectin")
    subscription = relationship(
        "Subscription", back_populates="user", uselist=False, lazy="selectin"
    )
    notification_prefs = relationship(
        "NotificationPreference",
        back_populates="user",
        uselist=False,
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_users_city_active", "city_id", "last_active_at"),
        Index("ix_users_city_visible", "city_id", "is_visible", "is_active"),
    )
