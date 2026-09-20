"""Shared pytest fixtures for all test modules.

Uses an in-memory SQLite database so tests run without a real Postgres instance.
All fixtures are async-compatible via pytest-asyncio (asyncio_mode = auto).

Design:
- Test env vars are pinned *before* ``src`` is imported so a developer's local
  ``.env`` (Postgres / dev.db / real API keys) can never leak into the suite.
- The suite shares the **application engine** (``src.platform.db.engine``) instead
  of building a second one. ``sqlite+aiosqlite:///:memory:`` uses ``StaticPool``
  (single connection), so tables created here are visible to service code that
  opens its own ``AsyncSessionLocal()`` — including the FastAPI app under test.
"""

from __future__ import annotations

import os

# --- pin test environment before importing src -----------------------------
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MOCK_MARKET", "true")
os.environ.setdefault("MARKET_FETCH_ALWAYS", "true")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("PERPLEXITY_API_KEY", "test-key")
os.environ.setdefault("OWNER_USER_ID", "user-test-001")

import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

import src.ai.memory.investor_profile  # noqa: E402,F401

# Import every ORM model module so Base.metadata knows all tables.
import src.ai.memory.models  # noqa: E402,F401
import src.ai.memory.user_behavior_log  # noqa: E402,F401
import src.briefing.models  # noqa: E402,F401
import src.core.evolution  # noqa: E402,F401
import src.core.models  # noqa: E402,F401
import src.portfolio.models  # noqa: E402,F401
import src.readmodel.models  # noqa: E402,F401
import src.thesis.models  # noqa: E402,F401
import src.watchlist.models  # noqa: E402,F401
from src.platform.db import Base  # noqa: E402
from src.platform.db import engine as _app_engine  # noqa: E402

if not str(_app_engine.url).startswith("sqlite"):  # pragma: no cover - safety net
    raise RuntimeError(
        f"Test suite must run on SQLite in-memory, got {_app_engine.url!r}. "
        "Check DATABASE_URL / .env."
    )


@pytest.fixture(scope="session")
def engine():
    """The application engine (SQLite in-memory, StaticPool)."""
    return _app_engine


@pytest.fixture(autouse=True)
async def create_tables(engine):
    """Create all tables before each test, drop after."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(autouse=True)
def memory_log_in_caller_session(monkeypatch):
    """Ghi episodic memory log vào session của caller thay vì session cách ly.

    Production: MemoryService.log_interaction mở AsyncSessionLocal riêng (Postgres →
    connection riêng, không ảnh hưởng caller). Test: SQLite :memory: StaticPool chỉ có
    MỘT connection → COMMIT/ROLLBACK của session cách ly (và task _maybe_consolidate)
    sẽ nuốt transaction của test (thesis_reviews biến mất). Patch để log đi cùng
    transaction test và tắt auto-consolidation.
    """
    from src.ai.memory.memory_service import MemoryService

    async def _log_in_caller(session, entry):
        if session is None:
            return None
        return await MemoryService._do_log(session, entry)

    async def _no_consolidate(user_id: str) -> None:
        return None

    monkeypatch.setattr(MemoryService, "log_interaction", staticmethod(_log_in_caller))
    monkeypatch.setattr(MemoryService, "_maybe_consolidate", staticmethod(_no_consolidate))


@pytest.fixture
async def session(engine) -> AsyncSession:
    """Yield a fresh AsyncSession per test, rolled back after."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
