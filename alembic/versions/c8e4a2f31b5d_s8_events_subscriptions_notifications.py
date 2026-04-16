"""s8_events_subscriptions_notifications

Session 8 — Events + Notifications + Subscriptions + Paystack.

Additions (toutes additives / nullable pour compat) :
- events : status (draft|published|full|ongoing|completed|cancelled), slug unique
- event_registrations : status, registered_via, checked_in_at,
  qr_code_hash unique, suggested_tags JSON
- users : onboarding_source, source_event_id (FK events), first_name
  (ghost users de la Porte 3)

Revision ID: c8e4a2f31b5d
Revises: 8a1f9e2d4b11
Create Date: 2026-04-16 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8e4a2f31b5d"
down_revision: Union[str, None] = "8a1f9e2d4b11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── events : status + slug ─────────────────────────────────────────
    op.add_column(
        "events",
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="draft",
        ),
    )
    op.add_column(
        "events",
        sa.Column("slug", sa.String(length=120), nullable=True),
    )
    op.create_index(
        op.f("ix_events_slug"), "events", ["slug"], unique=True
    )

    # ── event_registrations : status / via / checkin / QR / tags ──────
    op.add_column(
        "event_registrations",
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="registered",
        ),
    )
    op.add_column(
        "event_registrations",
        sa.Column(
            "registered_via",
            sa.String(length=10),
            nullable=False,
            server_default="app",
        ),
    )
    op.add_column(
        "event_registrations",
        sa.Column("checked_in_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "event_registrations",
        sa.Column("qr_code_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "event_registrations",
        sa.Column("suggested_tags", sa.JSON(), nullable=True),
    )
    op.create_index(
        op.f("ix_event_registrations_qr_code_hash"),
        "event_registrations",
        ["qr_code_hash"],
        unique=True,
    )

    # ── users : onboarding_source + source_event_id + first_name ──────
    op.add_column(
        "users",
        sa.Column(
            "onboarding_source",
            sa.String(length=20),
            nullable=False,
            server_default="classic",
        ),
    )
    op.add_column(
        "users",
        sa.Column("source_event_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_source_event_id",
        "users",
        "events",
        ["source_event_id"],
        ["id"],
    )
    op.add_column(
        "users",
        sa.Column("first_name", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "first_name")
    op.drop_constraint("fk_users_source_event_id", "users", type_="foreignkey")
    op.drop_column("users", "source_event_id")
    op.drop_column("users", "onboarding_source")

    op.drop_index(
        op.f("ix_event_registrations_qr_code_hash"),
        table_name="event_registrations",
    )
    op.drop_column("event_registrations", "suggested_tags")
    op.drop_column("event_registrations", "qr_code_hash")
    op.drop_column("event_registrations", "checked_in_at")
    op.drop_column("event_registrations", "registered_via")
    op.drop_column("event_registrations", "status")

    op.drop_index(op.f("ix_events_slug"), table_name="events")
    op.drop_column("events", "slug")
    op.drop_column("events", "status")
