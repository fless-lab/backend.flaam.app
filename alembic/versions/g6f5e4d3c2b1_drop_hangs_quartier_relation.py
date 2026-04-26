"""drop hangs quartier relation_type

Revision ID: g6f5e4d3c2b1
Revises: f5a6b7c8d9e0
Create Date: 2026-04-26

`hangs` faisait doublon avec UserSpot (granularité plus fine via spots
réels avec catégorie + fidelity + check-ins). Cette migration supprime
toutes les rows UserQuartier(relation_type='hangs') pour éviter de la
data orpheline. Le code applicatif n'expose plus ce relation_type
(schemas, services, mobile UI).

PAS DE CHECK CONSTRAINT côté DB sur relation_type (Postgres TEXT) — le
contrôle est fait via le Literal Pydantic. Donc rien à faire au niveau
schéma, juste un cleanup des données.
"""
from alembic import op


revision = "g6f5e4d3c2b1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM user_quartiers WHERE relation_type = 'hangs'"
    )


def downgrade() -> None:
    # Pas de rollback : la data 'hangs' est perdue. Le code applicatif
    # n'accepte plus ce relation_type côté API, donc revenir en arrière
    # nécessiterait de réintroduire 'hangs' dans le Literal Pydantic et
    # toutes les structures associées avant de redéployer cette down.
    pass
