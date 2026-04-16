"""s10_admin_and_premium_downgrade

Session 10 — Admin + RGPD + CI/CD.

Colonnes additives (nullable=False avec server_default → safe pour rows
existantes). Idempotent : skippe si la colonne existe déjà (belt &
suspenders, utile si la migration a été partiellement appliquée).

- users.is_admin (bool, default false)
- user_quartiers.is_active_in_matching (bool, default true)
- user_spots.is_active_in_matching (bool, default true)
- photos.is_deleted (bool, default false) — soft delete RGPD Phase 1

Revision ID: b2d7e9c1f4a0
Revises: a1b2c3d4e5f6
Create Date: 2026-04-17 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2d7e9c1f4a0"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("users", "is_admin"):
        op.add_column(
            "users",
            sa.Column(
                "is_admin",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    if not _has_column("user_quartiers", "is_active_in_matching"):
        op.add_column(
            "user_quartiers",
            sa.Column(
                "is_active_in_matching",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )

    if not _has_column("user_spots", "is_active_in_matching"):
        op.add_column(
            "user_spots",
            sa.Column(
                "is_active_in_matching",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )

    if not _has_column("photos", "is_deleted"):
        op.add_column(
            "photos",
            sa.Column(
                "is_deleted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    if _has_column("photos", "is_deleted"):
        op.drop_column("photos", "is_deleted")
    if _has_column("user_spots", "is_active_in_matching"):
        op.drop_column("user_spots", "is_active_in_matching")
    if _has_column("user_quartiers", "is_active_in_matching"):
        op.drop_column("user_quartiers", "is_active_in_matching")
    if _has_column("users", "is_admin"):
        op.drop_column("users", "is_admin")
