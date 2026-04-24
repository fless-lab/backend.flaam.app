"""safety_6_emergency_sessions

Revision ID: 93371d2a6db7
Revises: e5a3b7c2d1f9
Create Date: 2026-04-24 03:57:31.697691

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '93371d2a6db7'
down_revision: Union[str, None] = 'e5a3b7c2d1f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'emergency_sessions',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('partner_user_id', sa.UUID(), nullable=True),
        sa.Column('match_id', sa.UUID(), nullable=True),
        sa.Column('meeting_place', sa.String(length=200), nullable=True),
        sa.Column('latitude', sa.Float(), nullable=True),
        sa.Column('longitude', sa.Float(), nullable=True),
        sa.Column('contacts_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('hours', sa.Float(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('end_reason', sa.String(length=32), nullable=True),
        sa.Column('panic_triggered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['match_id'], ['matches.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['partner_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_emergency_sessions_active',
        'emergency_sessions',
        ['user_id', 'ended_at'],
        unique=False,
        postgresql_where='ended_at IS NULL',
    )
    op.create_index(
        op.f('ix_emergency_sessions_match_id'),
        'emergency_sessions',
        ['match_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_emergency_sessions_partner_user_id'),
        'emergency_sessions',
        ['partner_user_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_emergency_sessions_user_id'),
        'emergency_sessions',
        ['user_id'],
        unique=False,
    )
    op.create_index(
        'ix_emergency_sessions_user_started',
        'emergency_sessions',
        ['user_id', 'started_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_emergency_sessions_user_started', table_name='emergency_sessions')
    op.drop_index(op.f('ix_emergency_sessions_user_id'), table_name='emergency_sessions')
    op.drop_index(op.f('ix_emergency_sessions_partner_user_id'), table_name='emergency_sessions')
    op.drop_index(op.f('ix_emergency_sessions_match_id'), table_name='emergency_sessions')
    op.drop_index(
        'ix_emergency_sessions_active',
        table_name='emergency_sessions',
        postgresql_where='ended_at IS NULL',
    )
    op.drop_table('emergency_sessions')
