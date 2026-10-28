"""Alembic environment (P1.W1.5, plan §2.3).

Target metadata is ``autobridge.control.models.Base``. The database URL
comes from ``DATABASE_URL`` (see :func:`autobridge.control.models.default_database_url`);
``alembic.ini`` holds a local-dev default only. SQLite now, Postgres-ready:
the migration uses only portable types.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from autobridge.control.models import Base, default_database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# A programmatic Config (startup migrator, tests) may already carry the URL:
# keep it. An ini-loaded Config keeps the historical behavior: DATABASE_URL
# (or the local-dev default) wins over the ini value.
if config.config_file_name is None and config.get_main_option("sqlalchemy.url"):
    pass
else:
    config.set_main_option("sqlalchemy.url", default_database_url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a connection)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (needs a database connection)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
