from __future__ import annotations

import uuid

from geoalchemy2 import Geometry
from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class Spot(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "spots"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)

    city_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cities.id"), nullable=False
    )

    location = mapped_column(
        Geometry(geometry_type="POINT", srid=4326), nullable=False
    )
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)

    google_place_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    total_checkins: Mapped[int] = mapped_column(Integer, default=0)
    total_users: Mapped[int] = mapped_column(Integer, default=0)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    social_weight: Mapped[float] = mapped_column(Float, default=1.0)

    city = relationship("City", back_populates="spots")

    __table_args__ = (
        Index("ix_spots_location", "location", postgresql_using="gist"),
        Index("ix_spots_city_category", "city_id", "category"),
        Index("ix_spots_city_active", "city_id", "is_active"),
    )
