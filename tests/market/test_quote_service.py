"""Unit tests for QuoteService + MockAdapter."""

from __future__ import annotations

import pytest

from src.market.quote_service import Quote, QuoteService, QuoteServiceNotConfiguredError
from src.market.adapters.mock import MockAdapter, _make_mock_quote


# ---------------------------------------------------------------------------
# MockAdapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_adapter_returns_quote(mock_adapter):
    quote = await mock_adapter.fetch_quote("HPG")
    assert isinstance(quote, Quote)
    assert quote.ticker == "HPG"
    assert quote.price > 0
    assert quote.ceiling > quote.price or quote.ceiling >= quote.price


@pytest.mark.asyncio
async def test_mock_adapter_deterministic(mock_adapter):
    """Same ticker always returns same price."""
    q1 = await mock_adapter.fetch_quote("VNM")
    q2 = await mock_adapter.fetch_quote("VNM")
    assert q1.price == q2.price


@pytest.mark.asyncio
async def test_mock_adapter_different_tickers_differ(mock_adapter):
    q_hpg = await mock_adapter.fetch_quote("HPG")
    q_vnm = await mock_adapter.fetch_quote("VNM")
    assert q_hpg.price != q_vnm.price


@pytest.mark.asyncio
async def test_mock_adapter_fail_ticker(failing_adapter):
    with pytest.raises(ValueError, match="ERR"):
        await failing_adapter.fetch_quote("ERR")


@pytest.mark.asyncio
async def test_mock_adapter_bulk(mock_adapter):
    quotes = await mock_adapter.fetch_bulk_quotes(["HPG", "VNM", "FPT"])
    assert len(quotes) == 3
    tickers = {q.ticker for q in quotes}
    assert tickers == {"HPG", "VNM", "FPT"}


# ---------------------------------------------------------------------------
# QuoteService
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quote_service_get_quote(quote_service):
    quote = await quote_service.get_quote("hpg")  # lowercase normalised
    assert quote.ticker == "HPG"


@pytest.mark.asyncio
async def test_quote_service_bulk(quote_service):
    quotes = await quote_service.get_bulk_quotes(["hpg", "vnm"])
    assert len(quotes) == 2


@pytest.mark.asyncio
async def test_quote_service_no_adapter_raises():
    svc = QuoteService(adapter=None)
    with pytest.raises(QuoteServiceNotConfiguredError):
        await svc.get_quote("HPG")


# ---------------------------------------------------------------------------
# Quote domain helpers
# ---------------------------------------------------------------------------


def test_quote_is_ceiling():
    q = _make_mock_quote("AAA")
    # ceiling is above price in mock; not at ceiling
    assert not q.is_ceiling


def test_quote_format_price():
    q = _make_mock_quote("HPG")
    formatted = q.format_price()
    assert "," in formatted  # thousands separator
    assert "." not in formatted  # no decimal


def test_quote_format_change_positive():
    q = _make_mock_quote("HPG")
    result = q.format_change()
    assert result.startswith("+")
