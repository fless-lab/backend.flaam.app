"""travel gps confirmed at

Revision ID: k0j9i8h7g6f5
Revises: j9i8h7g6f5e4
Create Date: 2026-04-27

Confirmation GPS passive du mode voyage. Set quand la position de l'user
est détectée à <30km du centre de travel_city via un endpoint qui reçoit
déjà du GPS (check-in spot, scan flame). Le badge "Confirmé" est affiché
sur le profil public si confirmed_at < 24h.

Pas d'impact algo en Phase 1 — juste une preuve visuelle de présence
réelle. Phase 2 (si data le justifie) : petit boost ×1.10 sur les
voyageurs confirmés.
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "k0j9i8h7g6f5"
down_revision = "j9i8h7g6f5e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "travel_gps_confirmed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "travel_gps_confirmed_at")
