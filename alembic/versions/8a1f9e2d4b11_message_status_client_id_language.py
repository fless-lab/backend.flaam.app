"""message_status_client_id_language

Session 7 :
- Remplace Message.is_read (Bool) par Message.status (String)
  valeurs : sent | delivered | read | expired
- Convertit read_at en DateTime(timezone=True)
- Ajoute client_message_id (String(64)) + index unique (sender_id, client_message_id)
- Ajoute language_detected (String(10))

Revision ID: 8a1f9e2d4b11
Revises: 7c4d82af1b5f
Create Date: 2026-04-16 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8a1f9e2d4b11"
down_revision: Union[str, None] = "7c4d82af1b5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── status ──
    op.add_column(
        "messages",
        sa.Column(
            "status",
            sa.String(length=15),
            nullable=False,
            server_default="sent",
        ),
    )
    # Backfill depuis is_read
    op.execute(
        "UPDATE messages SET status = CASE WHEN is_read THEN 'read' ELSE 'sent' END"
    )

    # ── read_at : bascule en DateTime(timezone=True) ──
    # Dans l'état actuel read_at est typé String non contraint (mapped_column
    # sans type explicite) → on drop + recreate.
    op.drop_column("messages", "read_at")
    op.add_column(
        "messages",
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── Suppression de is_read ──
    op.drop_column("messages", "is_read")

    # ── client_message_id + index unique partiel ──
    op.add_column(
        "messages",
        sa.Column("client_message_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "uq_messages_sender_client_msg",
        "messages",
        ["sender_id", "client_message_id"],
        unique=True,
        postgresql_where=sa.text("client_message_id IS NOT NULL"),
    )

    # ── language_detected ──
    op.add_column(
        "messages",
        sa.Column("language_detected", sa.String(length=10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "language_detected")
    op.drop_index("uq_messages_sender_client_msg", table_name="messages")
    op.drop_column("messages", "client_message_id")

    op.add_column(
        "messages",
        sa.Column("is_read", sa.Boolean(), nullable=True),
    )
    op.execute("UPDATE messages SET is_read = (status = 'read')")
    op.alter_column("messages", "is_read", nullable=False, server_default=sa.text("false"))

    op.drop_column("messages", "read_at")
    op.add_column("messages", sa.Column("read_at", sa.String(), nullable=True))

    op.drop_column("messages", "status")
