from logging.config import fileConfig

from alembic import context
from geoalchemy2 import alembic_helpers
from sqlalchemy import engine_from_config, pool, text

from app.core.config import get_settings
from app.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Migrations use MIGRATION_DATABASE_URL when set (direct/session connection), else DATABASE_URL.
config.set_main_option("sqlalchemy.url", get_settings().migration_url.replace("%", "%%"))
target_metadata = Base.metadata

def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    """Ignore PostGIS-owned tables (tiger geocoder, topology, spatial_ref_sys) that exist only in the DB."""
    if type_ == "table" and reflected and name not in target_metadata.tables:
        return False
    return alembic_helpers.include_object(obj, name, type_, reflected, compare_to)


_common = dict(
    target_metadata=target_metadata,
    include_object=include_object,
    process_revision_directives=alembic_helpers.writer,
    render_item=alembic_helpers.render_item,
)


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True, **_common)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"prepare_threshold": None},  # safe behind a transaction-mode pooler
    )
    with connectable.connect() as connection:
        if get_settings().db_search_path:  # e.g. tests run migrations inside an isolated schema
            connection.execute(text(f"SET search_path TO {get_settings().db_search_path}"))
            connection.commit()
        extra: dict[str, str] = {}
        if get_settings().db_search_path:  # keep the version table inside the isolated schema too
            extra["version_table_schema"] = get_settings().db_search_path.split(",")[0].strip()
        context.configure(connection=connection, **_common, **extra)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
