from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin


class QuartierProximity(Base, UUIDMixin):
    __tablename__ = "quartier_proximities"

    quartier_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quartiers.id"), nullable=False
    )
    quartier_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quartiers.id"), nullable=False
    )
    proximity_score: Mapped[float] = mapped_column(Float, nullable=False)
    distance_km: Mapped[float] = mapped_column(Float, nullable=False)

    quartier_a = relationship("Quartier", foreign_keys=[quartier_a_id], lazy="selectin")
    quartier_b = relationship("Quartier", foreign_keys=[quartier_b_id], lazy="selectin")

    __table_args__ = (
        Index("uq_quartier_proximity", "quartier_a_id", "quartier_b_id", unique=True),
        Index("ix_qp_quartier_a", "quartier_a_id"),
        Index("ix_qp_quartier_b", "quartier_b_id"),
        CheckConstraint(
            "proximity_score >= 0 AND proximity_score <= 1",
            name="ck_proximity_range",
        ),
        CheckConstraint("quartier_a_id < quartier_b_id", name="ck_quartier_order"),
    )
