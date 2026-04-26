"""user travel mode + city change cooldown

Revision ID: j9i8h7g6f5e4
Revises: i8h7g6f5e4d3
Create Date: 2026-04-27

Mode voyage temporaire :
  - travel_city_id, travel_started_at, travel_until : voyage actif
  - travel_activations_count_30d, travel_window_start : limite 2/30j
  - travel_extension_used : flag prolongation +7j (1× par session)
Cooldown ville principale :
  - city_changed_at : date du dernier changement (limite 1×/30j)

La ville principale (city_id) reste stable. Quand travel_city_id est
non null ET travel_until > now, le feed/discovery utilise travel_city_id
comme ville de référence.
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "j9i8h7g6f5e4"
down_revision = "i8h7g6f5e4d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "travel_city_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_users_travel_city_id",
        "users",
        "cities",
        ["travel_city_id"],
        ["id"],
    )
    op.add_column(
        "users",
        sa.Column("travel_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("travel_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "travel_activations_count_30d",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "travel_window_start",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "travel_extension_used",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "users",
        sa.Column("city_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Index partiel : seuls les voyages actifs sont scannés par la Celery
    # task d'expiration et les requêtes feed.
    op.create_index(
        "ix_users_travel_active",
        "users",
        ["travel_city_id", "travel_until"],
        postgresql_where=sa.text("travel_city_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_travel_active", table_name="users")
    op.drop_constraint("fk_users_travel_city_id", "users", type_="foreignkey")
    op.drop_column("users", "city_changed_at")
    op.drop_column("users", "travel_extension_used")
    op.drop_column("users", "travel_window_start")
    op.drop_column("users", "travel_activations_count_30d")
    op.drop_column("users", "travel_until")
    op.drop_column("users", "travel_started_at")
    op.drop_column("users", "travel_city_id")
