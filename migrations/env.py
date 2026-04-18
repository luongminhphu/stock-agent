"""Alembic env.py — async-compatible with SQLAlchemy asyncpg.

Owner: platform segment.

Key decisions:
- Uses AsyncEngine from src.platform.db (same engine as app).
- Imports ALL ORM models before running migrations so Alembic
  can detect schema changes via autogenerate.
- DATABASE_URL is read from environment at runtime — never from alembic.ini.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from src.platform.config import settings
from src.platform.db import Base

# Import ALL models so their tables are registered on Base.metadata
# before autogenerate runs. Add new model modules here as segments grow.
from src.thesis.models import (  # noqa: F401
    Assumption,
    Catalyst,
    Thesis,
    ThesisReview,
    ThesisSnapshot,
)
from src.watchlist.models import (  # noqa: F401
    Alert,
    Reminder,
    WatchlistItem,
)

# Alembic Config object (gives access to .ini values)
config = context.config

# Inject real DATABASE_URL from pydantic settings (overrides blank in alembic.ini)
config.set_main_option("sqlalchemy.url", settings.database_url)

# Setup Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata target for autogenerate
target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Offline mode — generate SQL script without DB connection
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode — run against live async connection
# ---------------------------------------------------------------------------


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # no pool for migration runs
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
