"""user_flame + flame_scan_enabled + flame_scans_received_max

Revision ID: b8c1d2e3f4a5
Revises: a7b1c2d3e4f5
Create Date: 2026-04-25 14:00:00.000000

Insta-match QR :
- Table user_flames : 1 ligne par user, stocke qr_token rotatif 24h.
- Champs users : flame_scan_enabled (toggle sécurité, default true) et
  flame_scans_received_max (plafond par jour, default 10, user peut
  baisser jusqu'à 1).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8c1d2e3f4a5'
down_revision: Union[str, None] = 'a7b1c2d3e4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Toggle + plafond reçus côté users.
    op.add_column(
        'users',
        sa.Column(
            'flame_scan_enabled',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('true'),
        ),
    )
    op.add_column(
        'users',
        sa.Column(
            'flame_scans_received_max',
            sa.Integer(),
            nullable=False,
            server_default=sa.text('10'),
        ),
    )

    # Table user_flames.
    op.create_table(
        'user_flames',
        sa.Column('id', sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('qr_token', sa.String(length=64), nullable=False),
        sa.Column('rotated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('user_id', name='uq_user_flames_user_id'),
        sa.UniqueConstraint('qr_token', name='uq_user_flames_qr_token'),
    )
    op.create_index('ix_user_flames_qr_token', 'user_flames', ['qr_token'])


def downgrade() -> None:
    op.drop_index('ix_user_flames_qr_token', 'user_flames')
    op.drop_table('user_flames')
    op.drop_column('users', 'flame_scans_received_max')
    op.drop_column('users', 'flame_scan_enabled')
