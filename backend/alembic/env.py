"""Alembic migration environment (async).

The database URL and target metadata are sourced from the application itself so migrations
and the running app can never drift apart:

* URL comes from :func:`app.core.config.get_settings` (no credentials in the repo).
* ``target_metadata`` is the shared :data:`app.db.base.metadata`; importing
  ``app.models`` registers every ORM table on it (models land in Phase 2+).
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.base import metadata as target_metadata

# Importing the models package registers all tables on the shared metadata.
# It is a namespace package in Phase 1 (no models yet); the import is safe and future-proof.
import app.models  # noqa: F401,E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the runtime URL (psycopg/async) from application settings.
config.set_main_option("sqlalchemy.url", get_settings().control_db.dsn())


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
