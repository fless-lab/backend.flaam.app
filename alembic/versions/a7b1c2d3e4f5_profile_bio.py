"""profile_bio

Revision ID: a7b1c2d3e4f5
Revises: 93371d2a6db7
Create Date: 2026-04-25 12:00:00.000000

Add a free-text `bio` field on Profile (max 500 chars). Replaces the
prompts UX on the front; prompts column kept for legacy data + dead
code paths.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7b1c2d3e4f5'
down_revision: Union[str, None] = '93371d2a6db7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'profiles',
        sa.Column('bio', sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('profiles', 'bio')
