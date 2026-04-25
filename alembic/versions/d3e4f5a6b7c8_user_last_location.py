"""user.last_lat / last_lng / last_location_at

Revision ID: d3e4f5a6b7c8
Revises: c9d2e3f4a5b6
Create Date: 2026-04-25 16:30:00.000000

Localisation éphémère du user pour proximity check au scan.
- Set quand le mobile envoie sa position (PATCH /flame/me ou ping périodique)
- Considéré obsolète si > flame_scan_checkin_window_min (default 120 min)
- Pas de tracking GPS permanent — l'user contrôle quand il partage
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd3e4f5a6b7c8'
down_revision: Union[str, None] = 'c9d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('last_lat', sa.Float(), nullable=True))
    op.add_column('users', sa.Column('last_lng', sa.Float(), nullable=True))
    op.add_column(
        'users',
        sa.Column('last_location_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('users', 'last_location_at')
    op.drop_column('users', 'last_lng')
    op.drop_column('users', 'last_lat')
