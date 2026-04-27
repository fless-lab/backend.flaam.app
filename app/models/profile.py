from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import CheckConstraint, Date, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class Profile(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    display_name: Mapped[str] = mapped_column(String(50), nullable=False)
    birth_date: Mapped[date] = mapped_column(Date, nullable=False)
    gender: Mapped[str] = mapped_column(String(20), nullable=False)
    seeking_gender: Mapped[str] = mapped_column(String(20), nullable=False)

    intention: Mapped[str | None] = mapped_column(String(30), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Bio libre + prompts (max 3) : champs d'affichage non scorés.
    bio: Mapped[str | None] = mapped_column(String(500), nullable=True)
    prompts: Mapped[dict] = mapped_column(JSONB, default=list)

    tags: Mapped[list] = mapped_column(JSONB, default=list)

    # languages : affichage uniquement, pas dans le score matching.
    languages: Mapped[list] = mapped_column(JSONB, default=list)

    seeking_age_min: Mapped[int] = mapped_column(Integer, default=18)
    seeking_age_max: Mapped[int] = mapped_column(Integer, default=40)

    profile_completeness: Mapped[float] = mapped_column(Float, default=0.0)
    behavior_multiplier: Mapped[float] = mapped_column(Float, default=1.0)

    user = relationship("User", back_populates="profile")

    __table_args__ = (
        CheckConstraint("seeking_age_min >= 18", name="ck_seeking_age_min"),
        CheckConstraint(
            "seeking_age_max >= seeking_age_min", name="ck_seeking_age_range"
        ),
    )
