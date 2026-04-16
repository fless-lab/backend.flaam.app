from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class Message(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "messages"

    match_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matches.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    message_type: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    media_duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meetup_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Statut du message (§5.8) : sent → delivered → read → expired.
    # Remplace is_read/read_at depuis S7.
    status: Mapped[str] = mapped_column(
        String(15), default="sent", server_default="sent", nullable=False
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Dédup côté client — UUID/ULID généré par le mobile. Permet de
    # renvoyer le même Message si le client retry (3G instable).
    client_message_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    # Détection de langue — laissé null au MVP, utilisé par les
    # modèles de modération par langue (voir docs/flaam-ai-scoping.md).
    language_detected: Mapped[str | None] = mapped_column(String(10), nullable=True)

    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)

    match = relationship("Match", back_populates="messages")

    __table_args__ = (
        Index("ix_messages_match", "match_id", "created_at"),
        Index("ix_messages_sender", "sender_id"),
        # Dédup DB-level : même client_message_id pour un même sender
        # = même message. Complète le verrou Redis (source de vérité).
        Index(
            "uq_messages_sender_client_msg",
            "sender_id",
            "client_message_id",
            unique=True,
            postgresql_where="client_message_id IS NOT NULL",
        ),
    )
