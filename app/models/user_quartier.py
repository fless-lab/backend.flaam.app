from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class UserQuartier(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "user_quartiers"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    quartier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quartiers.id"), nullable=False
    )

    relation_type: Mapped[str] = mapped_column(String(15), nullable=False)
    is_primary: Mapped[bool] = mapped_column(default=False)
    # Gel doux premium → False désactive l'entrée pour le matching sans la
    # supprimer (spec §business-model : premium expiré = gel, pas suppression).
    is_active_in_matching: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )

    user = relationship("User", back_populates="user_quartiers")
    quartier = relationship("Quartier", lazy="selectin")

    __table_args__ = (
        Index(
            "uq_user_quartier",
            "user_id",
            "quartier_id",
            "relation_type",
            unique=True,
        ),
        Index("ix_user_quartiers_quartier", "quartier_id"),
        Index("ix_user_quartiers_user_type", "user_id", "relation_type"),
    )
