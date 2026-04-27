"""mfa lockout : pin_failed_attempts + pin_locked_until

Revision ID: n3m2l1k0j9i8
Revises: m2l1k0j9i8h7
Create Date: 2026-04-27

Cf #211 — anti-bruteforce sur le PIN MFA.
- 5 échecs consécutifs → lock 15 min
- 10 échecs consécutifs → lock 1h
Reset du compteur à chaque vérification réussie.
"""
from alembic import op
import sqlalchemy as sa


revision = "n3m2l1k0j9i8"
down_revision = "m2l1k0j9i8h7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "mfa_failed_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "mfa_locked_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "mfa_locked_until")
    op.drop_column("users", "mfa_failed_attempts")
