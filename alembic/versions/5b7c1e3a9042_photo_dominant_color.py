"""photo_dominant_color

Revision ID: 5b7c1e3a9042
Revises: 429a2716855a
Create Date: 2026-04-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5b7c1e3a9042'
down_revision: Union[str, None] = '429a2716855a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'photos',
        sa.Column('dominant_color', sa.String(length=7), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('photos', 'dominant_color')
