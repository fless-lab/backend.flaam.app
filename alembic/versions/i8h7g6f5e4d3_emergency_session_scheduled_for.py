"""emergency_session.scheduled_for

Revision ID: i8h7g6f5e4d3
Revises: h7g6f5e4d3c2
Create Date: 2026-04-26

Ajoute EmergencySession.scheduled_for (nullable). Si fourni, le timer
est en attente — la Celery task `activate_scheduled_timers` le bascule
en actif quand scheduled_for <= now. Permet d'armer un timer pour un
RDV futur (ex: meetup samedi 19h → timer scheduled à 18h30 samedi).
"""
import sqlalchemy as sa
from alembic import op


revision = "i8h7g6f5e4d3"
down_revision = "h7g6f5e4d3c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "emergency_sessions",
        sa.Column(
            "scheduled_for",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    # Index partiel : filtre les rows en attente d'activation.
    op.create_index(
        "ix_emergency_sessions_scheduled",
        "emergency_sessions",
        ["scheduled_for"],
        postgresql_where=sa.text(
            "scheduled_for IS NOT NULL AND ended_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_emergency_sessions_scheduled",
        table_name="emergency_sessions",
    )
    op.drop_column("emergency_sessions", "scheduled_for")
