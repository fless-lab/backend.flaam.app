"""event pricing + meta : is_free, price_xof, dress_code, important_notes

Revision ID: m2l1k0j9i8h7
Revises: l1k0j9i8h7g6
Create Date: 2026-04-27

Cf #200 — un event peut être gratuit ou payant (price_xof en FCFA),
avec un éventuel code vestimentaire suggéré et des "à savoir" libres
(RSVP requis, âge mini, etc.).

Default is_free=True : tous les events existants restent gratuits
jusqu'à ce qu'un admin le toggle.
"""
from alembic import op
import sqlalchemy as sa


revision = "m2l1k0j9i8h7"
down_revision = "l1k0j9i8h7g6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column(
            "is_free", sa.Boolean(), nullable=False, server_default="true"
        ),
    )
    op.add_column(
        "events",
        sa.Column("price_xof", sa.Integer(), nullable=True),
    )
    op.add_column(
        "events",
        sa.Column("dress_code", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "events",
        sa.Column("important_notes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("events", "important_notes")
    op.drop_column("events", "dress_code")
    op.drop_column("events", "price_xof")
    op.drop_column("events", "is_free")
