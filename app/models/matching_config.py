from __future__ import annotations

from sqlalchemy import Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class MatchingConfig(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "matching_configs"

    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    min_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (Index("ix_matching_config_category", "category"),)
