"""Alembic environment.

The database URL comes from app.config (i.e. STOCKSYNC_DATABASE_URL), never from
alembic.ini, so credentials cannot end up in a committed file.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.config import get_settings
from app.db.session import ensure_database_parent
from app.models import Base  # importing registers every model on Base.metadata

config = context.config
settings = get_settings()

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.database_url)
ensure_database_parent(settings)

target_metadata = Base.metadata

# SQLite cannot ALTER a column, drop a column before 3.35, or add most
# constraints. Batch mode works around that by recreating the table and copying
# the data. It relies on constraints having deterministic names, which is what
# the naming convention in app/db/base.py provides.
#
# Enabled only for SQLite: on a server dialect a real ALTER is cheaper and
# does not rewrite the table.
RENDER_AS_BATCH = settings.is_sqlite


def render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    """Render custom column types as their underlying SQLAlchemy type.

    ``UtcDateTime`` is a TypeDecorator whose only job is normalising tzinfo in
    Python; the emitted DDL is exactly ``DateTime(timezone=True)``. Rendering it
    by name would make every migration import ``app.models.base``, so a
    migration applied months from now would depend on today's application code
    still existing and behaving the same way. Migrations should be frozen
    artefacts — returning False lets Alembic use the default renderer.
    """
    if type_ == "type" and type(obj).__name__ == "UtcDateTime":
        # sa is already imported by the migration template.
        return "sa.DateTime(timezone=True)"
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        render_as_batch=RENDER_AS_BATCH,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Catch column type and default drift, not just added/dropped tables.
            compare_type=True,
            compare_server_default=True,
            render_as_batch=RENDER_AS_BATCH,
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
