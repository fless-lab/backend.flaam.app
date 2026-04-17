from __future__ import annotations

"""
DailyKpi — métriques agrégées quotidiennes (§29, S13).

Chaque ligne = (date, city_id, metric_name, value).
city_id = None → métrique globale (toutes villes confondues).

Upsert idempotent via UniqueConstraint(date, city_id, metric).
Le task Celery compute_daily_kpis tourne à 00h30 UTC chaque jour et
insère/met à jour les lignes du jour J-1.

Métriques (S13 MVP) : signups, signups_completed, daily_active,
likes, matches, messages, premium_count, reports.
"""

import uuid
from datetime import date

from sqlalchemy import Date, Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin


class DailyKpi(Base, UUIDMixin):
    __tablename__ = "daily_kpis"

    date: Mapped[date] = mapped_column(Date, nullable=False)
    city_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cities.id"), nullable=True
    )
    metric: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        Index(
            "uq_daily_kpi",
            "date",
            "city_id",
            "metric",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )
