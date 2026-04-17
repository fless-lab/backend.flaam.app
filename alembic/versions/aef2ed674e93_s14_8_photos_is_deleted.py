"""s14_8_photos_is_deleted

Revision ID: aef2ed674e93
Revises: 4cb2325d587b
Create Date: 2026-04-17 11:57:22.703947

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aef2ed674e93'
down_revision: Union[str, None] = '4cb2325d587b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('photos', sa.Column('is_deleted', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('photos', 'is_deleted')
