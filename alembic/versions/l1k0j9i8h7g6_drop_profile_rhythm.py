"""drop profile.rhythm column

Revision ID: l1k0j9i8h7g6
Revises: k0j9i8h7g6f5
Create Date: 2026-04-27

Le champ rhythm (early_bird/night_owl/flexible) est retiré du modèle :
- Pas de signal mesurable (data trop éparse, déjà à poids 0 dans v3)
- Question d'onboarding sans valeur produit
- Décision : mieux vaut ne plus collecter

Ne casse rien côté algo : poids déjà 0 depuis v3, donc retrait neutre.
"""
from alembic import op
import sqlalchemy as sa


revision = "l1k0j9i8h7g6"
down_revision = "k0j9i8h7g6f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("profiles", "rhythm")


def downgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column("rhythm", sa.String(length=20), nullable=True),
    )
