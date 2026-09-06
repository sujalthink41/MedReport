"""Alembic environment, wired for async SQLAlchemy and our Settings.

Two things this file does that the generated template does not:

1. **Takes the database URL from Settings**, not from ``alembic.ini``. One source of
   truth for credentials: the environment.
2. **Imports every ORM model** so ``Base.metadata`` is fully populated. Autogenerate
   compares the database against that metadata - a model that is not imported is
   invisible, and Alembic will happily generate a migration that DROPS its table.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

# Importing this module registers every table on Base.metadata. Do not remove it,
# even though nothing below references it by name.
from app.adapters.db import models  # noqa: F401
from app.adapters.db.base import Base
from app.core.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it.

    Useful when a DBA has to review and apply changes by hand, which is common in
    regulated environments - and healthcare often is one.
    """
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Without this, changing String(120) to String(255) is silently ignored by
        # autogenerate and your model and database quietly drift apart.
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_url(), poolclass=None)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
