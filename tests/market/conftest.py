"""Fixtures dùng chung cho tests/market.

Tests chạy bất kể giờ giao dịch → QuoteService dùng TradingHoursGuard(always=True),
tương đương MARKET_FETCH_ALWAYS=true ở runtime.
"""

from __future__ import annotations

import pytest

from src.market.adapters.mock import MockAdapter
from src.market.quote_service import QuoteService, TradingHoursGuard


@pytest.fixture
def always_open_guard() -> TradingHoursGuard:
    return TradingHoursGuard(always=True)


@pytest.fixture
def mock_adapter() -> MockAdapter:
    return MockAdapter()


@pytest.fixture
def failing_adapter() -> MockAdapter:
    return MockAdapter(fail_tickers={"ERR"})


@pytest.fixture
def quote_service(mock_adapter, always_open_guard) -> QuoteService:
    return QuoteService(mock_adapter, guard=always_open_guard)
