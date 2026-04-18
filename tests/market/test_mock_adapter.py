"""Unit tests for MockAdapter."""

from __future__ import annotations

import pytest

from src.market.adapters.mock import MockAdapter, _make_mock_quote


async def test_fetch_quote_returns_quote() -> None:
    adapter = MockAdapter()
    quote = await adapter.fetch_quote("HPG")
    assert quote.ticker == "HPG"
    assert quote.price > 0
    assert quote.ceiling > quote.ref_price
    assert quote.floor < quote.ref_price


async def test_same_ticker_deterministic() -> None:
    q1 = _make_mock_quote("VNM")
    q2 = _make_mock_quote("VNM")
    assert q1.price == q2.price
    assert q1.volume == q2.volume


async def test_different_tickers_different_prices() -> None:
    q1 = _make_mock_quote("HPG")
    q2 = _make_mock_quote("VNM")
    assert q1.price != q2.price


async def test_fail_tickers_raises() -> None:
    adapter = MockAdapter(fail_tickers={"ERR"})
    with pytest.raises(ValueError, match="ERR"):
        await adapter.fetch_quote("ERR")


async def test_bulk_quotes() -> None:
    adapter = MockAdapter()
    quotes = await adapter.fetch_bulk_quotes(["HPG", "VNM", "FPT"])
    assert len(quotes) == 3
    assert all(q.price > 0 for q in quotes)
