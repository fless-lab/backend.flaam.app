"""match.origin + match.event_id (instant_qr support)

Revision ID: c9d2e3f4a5b6
Revises: b8c1d2e3f4a5
Create Date: 2026-04-25 16:00:00.000000

Discriminer les matches feed-classiques vs insta-match QR.
- origin = "feed_like" (default backfill) | "instant_qr"
- event_id : event source si scan dans un event (FK Event nullable)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9d2e3f4a5b6'
down_revision: Union[str, None] = 'b8c1d2e3f4a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'matches',
        sa.Column(
            'origin',
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'feed_like'"),
        ),
    )
    op.add_column(
        'matches',
        sa.Column(
            'event_id',
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        'matches_event_id_fkey', 'matches', 'events',
        ['event_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index('ix_matches_origin', 'matches', ['origin'])


def downgrade() -> None:
    op.drop_index('ix_matches_origin', 'matches')
    op.drop_constraint('matches_event_id_fkey', 'matches', type_='foreignkey')
    op.drop_column('matches', 'event_id')
    op.drop_column('matches', 'origin')
