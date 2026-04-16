from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.db.base import Base
import app.models  # noqa: F401 — force model registration

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# PostGIS / tiger_geocoder / topology tables présentes dans la DB par
# l'image postgis/postgis. Elles arrivent avec object.schema=None à cause
# de search_path, d'où le filtrage par nom.
POSTGIS_TABLES = {
    # postgis core
    "spatial_ref_sys",
    # postgis_topology
    "topology", "layer",
    # postgis_tiger_geocoder — référence
    "addr", "addrfeat", "bg", "county", "county_lookup", "countysub_lookup",
    "cousub", "direction_lookup", "edges", "faces", "featnames",
    "geocode_settings", "geocode_settings_default",
    "loader_lookuptables", "loader_platform", "loader_variables",
    "pagc_gaz", "pagc_lex", "pagc_rules",
    "place", "place_lookup", "secondary_unit_lookup",
    "state", "state_lookup", "street_type_lookup",
    "tabblock", "tabblock20", "tract",
    "zcta5", "zip_lookup", "zip_lookup_all", "zip_lookup_base",
    "zip_state", "zip_state_loc",
}


INCLUDED_SCHEMAS = {"public"}


def include_name(name, type_, parent_names):
    # Bretelles : filtrage au name-listing (avant réflexion complète).
    if type_ == "schema":
        return name in INCLUDED_SCHEMAS or name is None
    if type_ == "table" and name in POSTGIS_TABLES:
        return False
    return True


def include_object(object, name, type_, reflected, compare_to):
    # Ceinture : filtrage au niveau de l'objet réfléchi.
    if type_ == "table" and name in POSTGIS_TABLES:
        return False
    if type_ == "table":
        schema = getattr(object, "schema", None)
        if schema not in (None, "public"):
            return False
    # On filtre aussi les index portés par ces tables
    if type_ == "index" and compare_to is None:
        table_name = getattr(getattr(object, "table", None), "name", None)
        if table_name in POSTGIS_TABLES:
            return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_name=include_name,
        include_object=include_object,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_name=include_name,
        include_object=include_object,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
