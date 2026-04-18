"""MockAdapter — deterministic fake quotes for tests and development.

Owner: market segment.

Generates stable Quote objects from ticker name without any HTTP calls.
Price is derived deterministically from the ticker string so the same
ticker always returns the same fake price across test runs.

Usage:
    adapter = MockAdapter()
    quote = await adapter.fetch_quote("HPG")
    assert quote.ticker == "HPG"
    assert quote.price > 0

    # Inject failure for specific tickers:
    adapter = MockAdapter(fail_tickers={"ERR"})
    await adapter.fetch_quote("ERR")  # raises ValueError
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.market.quote_service import MarketDataAdapter, Quote

_MOCK_BASE_PRICE = 30_000.0
_MOCK_CHANGE_PCT = 1.5


class MockAdapter(MarketDataAdapter):
    """Adapter that returns fake but deterministic Quote objects."""

    def __init__(self, fail_tickers: set[str] | None = None) -> None:
        self._fail_tickers: set[str] = fail_tickers or set()

    async def fetch_quote(self, ticker: str) -> Quote:
        if ticker in self._fail_tickers:
            raise ValueError(f"MockAdapter: forced failure for '{ticker}'.")
        return _make_mock_quote(ticker)

    async def fetch_bulk_quotes(self, tickers: list[str]) -> list[Quote]:
        return [await self.fetch_quote(t) for t in tickers]


def _make_mock_quote(ticker: str) -> Quote:
    """Generate a deterministic Quote from a ticker symbol."""
    # Stable price derived from sum of char codes — same ticker = same price
    seed = sum(ord(c) for c in ticker)
    price = _MOCK_BASE_PRICE + (seed % 50_000)
    change = price * (_MOCK_CHANGE_PCT / 100)
    ref_price = price - change
    ceiling = round(ref_price * 1.07, -2)  # HOSE +7%
    floor_ = round(ref_price * 0.93, -2)   # HOSE -7%

    return Quote(
        ticker=ticker,
        price=price,
        change=change,
        change_pct=_MOCK_CHANGE_PCT,
        volume=seed * 1000,
        value=price * seed * 1000,
        open=ref_price,
        high=price + change,
        low=ref_price - change,
        ref_price=ref_price,
        ceiling=ceiling,
        floor=floor_,
        timestamp=datetime.now(tz=timezone.utc),
    )
