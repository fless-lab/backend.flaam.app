"""photo_blurred_url

Revision ID: e5a3b7c2d1f9
Revises: 16fe0dc6384a
Create Date: 2026-04-18 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5a3b7c2d1f9'
down_revision: Union[str, None] = '16fe0dc6384a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'photos',
        sa.Column('blurred_url', sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('photos', 'blurred_url')
