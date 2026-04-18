"""Shared pytest fixtures for all test modules.

Uses an in-memory SQLite database so tests run without a real Postgres instance.
All fixtures are async-compatible via pytest-asyncio (asyncio_mode = auto).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.platform.db import Base

# SQLite in-memory — fast, no setup, no teardown
_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def engine():
    return create_async_engine(_TEST_DB_URL, echo=False)


@pytest.fixture(autouse=True)
async def create_tables(engine):
    """Create all tables before each test, drop after."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def session(engine) -> AsyncSession:
    """Yield a fresh AsyncSession per test, rolled back after."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
