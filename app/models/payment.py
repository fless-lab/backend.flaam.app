from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class Payment(Base, UUIDMixin, TimestampMixin):
    """
    Tracking de chaque tentative de paiement (succès ou échec).
    Une Subscription peut avoir plusieurs Payments dans son historique
    (paiement initial, renouvellements, retries après échec).

    State machine : initialized → pending → success | failed | timeout
    """

    __tablename__ = "payments"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="SET NULL"),
        nullable=True,
    )

    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="XOF", nullable=False)

    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_reference: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    payment_method: Mapped[str | None] = mapped_column(String(30), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), default="initialized", nullable=False
    )

    idempotency_key: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )

    initialized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    webhook_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    user = relationship("User")
    subscription = relationship("Subscription", backref="payments")

    __table_args__ = (Index("ix_payments_status", "status"),)
