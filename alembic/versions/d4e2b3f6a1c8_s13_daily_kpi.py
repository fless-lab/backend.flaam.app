"""s13_daily_kpi

Session 13 — Analytics KPIs.

Creation de la table `daily_kpis` pour persister les metriques
quotidiennes calculees par le task Celery compute_daily_kpis.

Revision ID: d4e2b3f6a1c8
Revises: c3f1a9d5e2b7
Create Date: 2026-04-17 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e2b3f6a1c8"
down_revision: Union[str, None] = "c3f1a9d5e2b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_kpis",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column(
            "city_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cities.id"),
            nullable=True,
        ),
        sa.Column("metric", sa.String(length=50), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
    )
    op.create_index(
        "uq_daily_kpi",
        "daily_kpis",
        ["date", "city_id", "metric"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_table("daily_kpis")
