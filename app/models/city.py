from __future__ import annotations

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class City(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "cities"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    country_name: Mapped[str] = mapped_column(String(100), nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    premium_price_monthly: Mapped[int] = mapped_column(Integer, nullable=False)
    premium_price_weekly: Mapped[int] = mapped_column(Integer, nullable=False)
    min_weekly_visibility: Mapped[int] = mapped_column(Integer, default=15)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    quartiers = relationship("Quartier", back_populates="city", lazy="selectin")
    spots = relationship("Spot", back_populates="city", lazy="selectin")
