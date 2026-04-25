"""user.flame_scan_verified_only + flame_scan_attempts

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-04-25 17:30:00.000000

- flame_scan_verified_only : si true côté target, refuse les scans
  d'un scanner non vérifié.
- flame_scan_attempts : log de chaque tentative (sécurité, historique).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f5a6b7c8d9e0'
down_revision: Union[str, None] = 'e4f5a6b7c8d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column(
            'flame_scan_verified_only',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )

    op.create_table(
        'flame_scan_attempts',
        sa.Column('id', sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('scanner_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('target_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('status', sa.String(length=40), nullable=False),
        sa.Column('scanner_lat', sa.Float(), nullable=True),
        sa.Column('scanner_lng', sa.Float(), nullable=True),
        sa.Column('event_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['scanner_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='SET NULL'),
    )
    op.create_index(
        'ix_flame_scan_attempts_target_at', 'flame_scan_attempts', ['target_id', 'at'],
    )
    op.create_index(
        'ix_flame_scan_attempts_scanner_at', 'flame_scan_attempts', ['scanner_id', 'at'],
    )


def downgrade() -> None:
    op.drop_index('ix_flame_scan_attempts_scanner_at', 'flame_scan_attempts')
    op.drop_index('ix_flame_scan_attempts_target_at', 'flame_scan_attempts')
    op.drop_table('flame_scan_attempts')
    op.drop_column('users', 'flame_scan_verified_only')
