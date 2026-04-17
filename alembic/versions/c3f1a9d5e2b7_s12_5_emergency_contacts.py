"""s12_5_emergency_contacts

Session 12.5 — Timer d'urgence complet.

Creation de la table `emergency_contacts` pour stocker les contacts
de confiance pre-enregistres par chaque user (max 3 par user, enforce
cote service).

Revision ID: c3f1a9d5e2b7
Revises: b2d7e9c1f4a0
Create Date: 2026-04-17 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3f1a9d5e2b7"
down_revision: Union[str, None] = "b2d7e9c1f4a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "emergency_contacts",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_emergency_contacts_user",
        "emergency_contacts",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_emergency_contacts_user", table_name="emergency_contacts"
    )
    op.drop_table("emergency_contacts")
