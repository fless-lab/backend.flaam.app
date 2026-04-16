"""s9_safety_reminders_targeted_likes

Session 9 — Safety + Contacts + Behavior + Config + Rate limiting +
Idempotency + Features A/B/C.

Additions (toutes additives / nullable ou avec server_default) :
- account_histories.blocked_by_count (int, default 0)
- notification_preferences.reply_reminders (bool, default true)
- matches.like_target_type / like_target_id / like_comment (nullable)

Revision ID: a1b2c3d4e5f6
Revises: c8e4a2f31b5d
Create Date: 2026-04-16 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "c8e4a2f31b5d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── account_histories.blocked_by_count ─────────────────────────────
    op.add_column(
        "account_histories",
        sa.Column(
            "blocked_by_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    # ── notification_preferences.reply_reminders ──────────────────────
    op.add_column(
        "notification_preferences",
        sa.Column(
            "reply_reminders",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )

    # ── matches : targeted like (Feature A) ────────────────────────────
    op.add_column(
        "matches",
        sa.Column("like_target_type", sa.String(length=10), nullable=True),
    )
    op.add_column(
        "matches",
        sa.Column("like_target_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "matches",
        sa.Column("like_comment", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("matches", "like_comment")
    op.drop_column("matches", "like_target_id")
    op.drop_column("matches", "like_target_type")
    op.drop_column("notification_preferences", "reply_reminders")
    op.drop_column("account_histories", "blocked_by_count")
