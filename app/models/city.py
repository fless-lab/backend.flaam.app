from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String
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

    # Diamètre géographique (km) — utilisé par le proximity dynamique
    # (#199 R&D Phase 4). Calculé périodiquement comme la max distance
    # entre 2 quartiers de la ville. Null = non calculé → on retombe sur
    # settings.geolocated_default_city_diameter_km.
    diameter_km: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Launch phases (MàJ villes/pays) ──
    # hidden | teaser | launch | growth | stable
    # Les villes hidden ne sont jamais retournées par l'API publique.
    phase: Mapped[str] = mapped_column(
        String(20), nullable=False, default="hidden", server_default="hidden"
    )
    country_flag: Mapped[str | None] = mapped_column(String(10), nullable=True)
    phone_prefix: Mapped[str | None] = mapped_column(String(5), nullable=True)
    display_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    waitlist_threshold: Mapped[int] = mapped_column(
        Integer, default=500, server_default="500"
    )

    quartiers = relationship("Quartier", back_populates="city", lazy="selectin")
    spots = relationship("Spot", back_populates="city", lazy="selectin")
