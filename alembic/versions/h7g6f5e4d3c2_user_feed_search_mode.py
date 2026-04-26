"""user feed_search_mode column

Revision ID: h7g6f5e4d3c2
Revises: g6f5e4d3c2b1
Create Date: 2026-04-26

Ajoute User.feed_search_mode (whole_city | specific_quartiers). Default
'whole_city' pour les users existants : ils sont ouverts à toute la
ville par défaut. À reconfigurer via SearchArea (mobile) si l'user
veut cibler des quartiers spécifiques.
"""
import sqlalchemy as sa
from alembic import op


revision = "h7g6f5e4d3c2"
down_revision = "g6f5e4d3c2b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "feed_search_mode",
            sa.String(length=30),
            nullable=False,
            server_default="whole_city",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "feed_search_mode")
