"""s13_photo_rejection_reason_to_text

Revision ID: 5a3bcc34094c
Revises: d4e2b3f6a1c8
Create Date: 2026-04-17 01:59:28.201813

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5a3bcc34094c'
down_revision: Union[str, None] = 'd4e2b3f6a1c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('photos', 'rejection_reason',
               existing_type=sa.VARCHAR(length=200),
               type_=sa.Text(),
               existing_nullable=True)


def downgrade() -> None:
    op.alter_column('photos', 'rejection_reason',
               existing_type=sa.Text(),
               type_=sa.VARCHAR(length=200),
               existing_nullable=True)
