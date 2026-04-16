"""city_phase_waitlist_invites

Revision ID: 7c4d82af1b5f
Revises: 5b7c1e3a9042
Create Date: 2026-04-16 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7c4d82af1b5f"
down_revision: Union[str, None] = "5b7c1e3a9042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── City : phase + country_flag + phone_prefix + display_order + threshold
    op.add_column(
        "cities",
        sa.Column(
            "phase",
            sa.String(length=20),
            nullable=False,
            server_default="hidden",
        ),
    )
    op.add_column(
        "cities",
        sa.Column("country_flag", sa.String(length=10), nullable=True),
    )
    op.add_column(
        "cities",
        sa.Column("phone_prefix", sa.String(length=5), nullable=True),
    )
    op.add_column(
        "cities",
        sa.Column(
            "display_order",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "cities",
        sa.Column(
            "waitlist_threshold",
            sa.Integer(),
            nullable=False,
            server_default="500",
        ),
    )

    # ── User : is_ambassador
    op.add_column(
        "users",
        sa.Column(
            "is_ambassador",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )

    # ── waitlist_entries
    op.create_table(
        "waitlist_entries",
        sa.Column("city_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("gender", sa.String(length=10), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="waiting"
        ),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invite_code_used", sa.String(length=20), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["city_id"], ["cities.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        "ix_waitlist_city_status", "waitlist_entries", ["city_id", "status"]
    )
    op.create_index(
        "ix_waitlist_city_gender", "waitlist_entries", ["city_id", "gender"]
    )
    op.create_index(
        "ix_waitlist_city_position", "waitlist_entries", ["city_id", "position"]
    )

    # ── invite_codes
    op.create_table(
        "invite_codes",
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("creator_id", sa.UUID(), nullable=False),
        sa.Column("city_id", sa.UUID(), nullable=False),
        sa.Column(
            "type", sa.String(length=20), nullable=False, server_default="standard"
        ),
        sa.Column("used_by_id", sa.UUID(), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["city_id"], ["cities.id"]),
        sa.ForeignKeyConstraint(["used_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index(
        op.f("ix_invite_codes_code"), "invite_codes", ["code"], unique=False
    )
    op.create_index("ix_invite_codes_creator", "invite_codes", ["creator_id"])
    op.create_index("ix_invite_codes_city", "invite_codes", ["city_id"])


def downgrade() -> None:
    op.drop_index("ix_invite_codes_city", table_name="invite_codes")
    op.drop_index("ix_invite_codes_creator", table_name="invite_codes")
    op.drop_index(op.f("ix_invite_codes_code"), table_name="invite_codes")
    op.drop_table("invite_codes")

    op.drop_index("ix_waitlist_city_position", table_name="waitlist_entries")
    op.drop_index("ix_waitlist_city_gender", table_name="waitlist_entries")
    op.drop_index("ix_waitlist_city_status", table_name="waitlist_entries")
    op.drop_table("waitlist_entries")

    op.drop_column("users", "is_ambassador")

    op.drop_column("cities", "waitlist_threshold")
    op.drop_column("cities", "display_order")
    op.drop_column("cities", "phone_prefix")
    op.drop_column("cities", "country_flag")
    op.drop_column("cities", "phase")
