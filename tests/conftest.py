"""Shared pytest fixtures for all segments.

Fixtures here are available to every test without explicit import.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.platform.db import Base
from src.market.adapters.mock import MockAdapter, _make_mock_quote
from src.market.quote_service import Quote, QuoteService


# ---------------------------------------------------------------------------
# Event loop (session-scoped for performance)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# In-memory SQLite database
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def engine():
    """Create all tables once per test session in :memory: SQLite."""
    _engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield _engine
    await _engine.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncGenerator[AsyncSession, None]:
    """Provide a clean transactional session per test (rolled back after)."""
    _factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with _factory() as sess:
        async with sess.begin():
            yield sess
            await sess.rollback()


# ---------------------------------------------------------------------------
# Market fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_adapter():
    return MockAdapter()


@pytest.fixture
def failing_adapter():
    """MockAdapter that fails for ticker 'ERR'."""
    return MockAdapter(fail_tickers={"ERR"})


@pytest.fixture
def quote_service(mock_adapter):
    return QuoteService(adapter=mock_adapter)


@pytest.fixture
def sample_quote() -> Quote:
    return _make_mock_quote("HPG")


# ---------------------------------------------------------------------------
# Thesis fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def thesis_factory():
    """Return a callable that builds Thesis ORM objects without DB."""
    from src.thesis.models import Thesis, ThesisStatus

    def _make(
        ticker="HPG",
        user_id="user_001",
        status=ThesisStatus.ACTIVE,
        entry_price=50_000.0,
        target_price=65_000.0,
        stop_loss=45_000.0,
        score=72.0,
    ) -> Thesis:
        return Thesis(
            id=1,
            ticker=ticker,
            user_id=user_id,
            title=f"Thesis on {ticker}",
            status=status,
            entry_price=entry_price,
            target_price=target_price,
            stop_loss=stop_loss,
            score=score,
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
        )

    return _make


# ---------------------------------------------------------------------------
# AI / Perplexity fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_perplexity_client():
    client = MagicMock()
    client.chat_completion = AsyncMock(return_value={
        "choices": [{"message": {"content": '{"verdict":"BULLISH","confidence":0.8,"risk_signals":[],"next_watch_items":[],"reasoning":"test"}'}}]
    })
    return client
