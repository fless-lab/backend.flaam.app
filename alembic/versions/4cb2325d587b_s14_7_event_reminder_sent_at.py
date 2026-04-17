"""s14_7_event_reminder_sent_at

Revision ID: 4cb2325d587b
Revises: 5a3bcc34094c
Create Date: 2026-04-17 09:47:23.474092

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4cb2325d587b'
down_revision: Union[str, None] = '5a3bcc34094c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('events', sa.Column('reminder_sent_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'reminder_sent_at')
