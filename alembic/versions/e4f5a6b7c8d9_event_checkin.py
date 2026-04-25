"""event_checkin table

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-04-25 17:00:00.000000

EventCheckin distinct de EventRegistration (inscription = intention,
checkin = présence GPS-vérifiée). Append-only.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e4f5a6b7c8d9'
down_revision: Union[str, None] = 'd3e4f5a6b7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'event_checkins',
        sa.Column('id', sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('event_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('lat', sa.Float(), nullable=False),
        sa.Column('lng', sa.Float(), nullable=False),
        sa.Column('verified', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_event_checkins_event_at', 'event_checkins', ['event_id', 'at'])
    op.create_index('ix_event_checkins_user', 'event_checkins', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_event_checkins_user', 'event_checkins')
    op.drop_index('ix_event_checkins_event_at', 'event_checkins')
    op.drop_table('event_checkins')
