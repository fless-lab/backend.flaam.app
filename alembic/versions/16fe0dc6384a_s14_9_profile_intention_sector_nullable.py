"""s14_9_profile_intention_sector_nullable

Revision ID: 16fe0dc6384a
Revises: aef2ed674e93
Create Date: 2026-04-17 23:18:24.092712

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '16fe0dc6384a'
down_revision: Union[str, None] = 'aef2ed674e93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('profiles', 'intention',
               existing_type=sa.VARCHAR(length=30),
               nullable=True)
    op.alter_column('profiles', 'sector',
               existing_type=sa.VARCHAR(length=30),
               nullable=True)


def downgrade() -> None:
    op.alter_column('profiles', 'sector',
               existing_type=sa.VARCHAR(length=30),
               nullable=False)
    op.alter_column('profiles', 'intention',
               existing_type=sa.VARCHAR(length=30),
               nullable=False)
