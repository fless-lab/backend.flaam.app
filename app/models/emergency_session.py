from __future__ import annotations

"""
EmergencySession (SAFETY-6).

Trace d'audit persistante de chaque timer d'urgence. Redis reste le
cache de run pour le task Celery (scan / SMS), mais la BD est la source
de vérité à des fins forensiques : en cas d'incident (panique, timer
non annulé, SMS envoyés à la famille), on peut toujours retrouver
l'état complet de la session hors TTL Redis.

Chaque ligne représente UN timer armé par un user. Elle est :
- Créée à l'armement (`start_emergency_timer`).
- Mise à jour à l'annulation (`cancel_emergency_timer` → ended_at,
  end_reason="cancelled").
- Mise à jour à la panique (`trigger_panic` → panic_triggered_at,
  end_reason="panic_triggered").
- Mise à jour à l'expiration SMS (task Celery → ended_at,
  end_reason="expired_sms_sent").

Le snapshot des contacts est figé à l'armement (même si l'user les
modifie ensuite, l'historique reste exact).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class EmergencySession(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "emergency_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Le partenaire du rendez-vous — renseigné si le timer est lancé
    # depuis un chat (le mobile passe match_id). ON DELETE SET NULL pour
    # conserver la session même si le user disparaît.
    partner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    match_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    meeting_place: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Snapshot figé au moment de l'armement :
    # [{"id": "<uuid>" | null, "name": "...", "phone": "+228..."}]
    contacts_snapshot: Mapped[list] = mapped_column(
        JSONB, default=list, nullable=False
    )

    hours: Mapped[float] = mapped_column(Float, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    end_reason: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    panic_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "ix_emergency_sessions_user_started",
            "user_id",
            "started_at",
        ),
        Index(
            "ix_emergency_sessions_active",
            "user_id",
            "ended_at",
            postgresql_where="ended_at IS NULL",
        ),
    )
