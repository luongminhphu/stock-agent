"""Alembic env.py — async SQLAlchemy setup.

Supports both online (real DB) and offline (SQL script) migration modes.
DB URL is always read from src.platform.config.settings — never hardcoded.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

import src.ai.memory.investor_profile  # noqa: F401
import src.ai.memory.models  # noqa: F401
import src.ai.memory.user_behavior_log  # noqa: F401
import src.briefing.models  # noqa: F401
import src.core.evolution  # noqa: F401
import src.core.models  # noqa: F401
import src.market.models  # noqa: F401 — Wave F2: trend snapshot/prediction tables (từ readmodel)
import src.portfolio.models  # noqa: F401
import src.thesis.models  # noqa: F401
import src.watchlist.models  # noqa: F401
from src.platform.config import settings

# Load all ORM models so Alembic can see their metadata.
# Add new model imports here as new segments are added.
from src.platform.db import Base  # noqa: F401 — registers Base.metadata

# Alembic Config object
config = context.config

# Logging setup from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate
target_metadata = Base.metadata


def get_url() -> str:
    return settings.database_url


def _strip_comment_only_ops(context_, revision, directives) -> None:
    """Bỏ các AlterColumnOp chỉ đổi ``comment`` khỏi autogenerate / ``alembic check``.

    Column comment là tài liệu, không phải schema behavior; để nguyên sẽ làm
    ``alembic check`` (CI job Migrations) báo đỏ vì hàng chục diff vô hại và che
    mất drift thật (index/column thiếu). Op có kèm đổi type/nullable/default vẫn giữ.
    """
    from alembic.operations import ops

    def _keep(op) -> bool:
        if not isinstance(op, ops.AlterColumnOp):
            return True
        # Alembic: nullable/name/type mặc định None; server_default/comment mặc định False.
        structural = (
            op.modify_type is not None
            or op.modify_nullable is not None
            or op.modify_name is not None
            or op.modify_server_default is not False
        )
        return structural or op.modify_comment is False

    def _prune(container) -> None:
        # ModifyTableOps lồng trong UpgradeOps/DowngradeOps → duyệt đệ quy.
        kept = []
        for op in container.ops:
            if isinstance(op, ops.OpContainer):
                _prune(op)
                if op.ops:
                    kept.append(op)
            elif _keep(op):
                kept.append(op)
        container.ops = kept

    if not directives:
        return
    script = directives[0]
    for group in (*script.upgrade_ops_list, *script.downgrade_ops_list):
        _prune(group)
    if script.upgrade_ops.is_empty():
        directives[:] = []


def run_migrations_offline() -> None:
    """Generate SQL script without a live DB connection."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        process_revision_directives=_strip_comment_only_ops,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations against a live async DB connection."""
    connectable = create_async_engine(get_url())
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
