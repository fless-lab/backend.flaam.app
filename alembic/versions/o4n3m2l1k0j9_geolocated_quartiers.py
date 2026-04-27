"""geolocated quartiers : quartiers.area + cities.diameter_km

Revision ID: o4n3m2l1k0j9
Revises: n3m2l1k0j9i8
Create Date: 2026-04-27

R&D Phase 1 (#215 / #199 epic) — ajout des colonnes nécessaires au
nouveau système géolocalisé. Pas de bascule de l'algo : tant que
settings.geolocated_quartiers_enabled = False, les colonnes sont juste
remplies par le seed et lues par les futures phases.

- quartiers.area : Polygon PostGIS (WGS84) — zone réelle du quartier.
  Nullable : un quartier peut rester legacy (lat/lng seulement).
- cities.diameter_km : utilisé par le proximity dynamique pour
  normaliser la distance selon la taille de la ville. Nullable :
  fallback sur settings.geolocated_default_city_diameter_km.
"""
from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geometry


revision = "o4n3m2l1k0j9"
down_revision = "n3m2l1k0j9i8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "quartiers",
        sa.Column(
            "area",
            Geometry(geometry_type="POLYGON", srid=4326),
            nullable=True,
        ),
    )
    # Index spatial pour les futures requêtes ST_Intersects/ST_Distance.
    op.create_index(
        "ix_quartiers_area_gist",
        "quartiers",
        ["area"],
        postgresql_using="gist",
    )

    op.add_column(
        "cities",
        sa.Column("diameter_km", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cities", "diameter_km")
    op.drop_index("ix_quartiers_area_gist", table_name="quartiers")
    op.drop_column("quartiers", "area")
