from __future__ import annotations

import uuid

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class Quartier(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "quartiers"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    city_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cities.id"), nullable=False
    )
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    city = relationship("City", back_populates="quartiers")
