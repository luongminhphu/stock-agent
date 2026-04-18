import pytest
from datetime import datetime

from src.market.quote_service import (
    MarketDataAdapter,
    Quote,
    QuoteService,
    QuoteServiceNotConfiguredError,
)


def _make_quote(ticker: str, price: float) -> Quote:
    return Quote(
        ticker=ticker,
        price=price,
        change=1000.0,
        change_pct=1.19,
        volume=1_000_000,
        value=85_400_000_000.0,
        open=84_000.0,
        high=86_000.0,
        low=83_500.0,
        ref_price=84_400.0,
        ceiling=90_300.0,
        floor=78_500.0,
        timestamp=datetime.now(),
    )


async def test_get_quote_without_adapter_raises() -> None:
    svc = QuoteService()
    with pytest.raises(QuoteServiceNotConfiguredError):
        await svc.get_quote("VNM")


async def test_get_quote_with_mock_adapter() -> None:
    class MockAdapter(MarketDataAdapter):
        async def fetch_quote(self, ticker: str) -> Quote:
            return _make_quote(ticker, 85_400.0)

        async def fetch_bulk_quotes(self, tickers: list[str]) -> list[Quote]:
            return [_make_quote(t, 85_400.0) for t in tickers]

    svc = QuoteService(adapter=MockAdapter())
    quote = await svc.get_quote("VNM")
    assert quote.ticker == "VNM"
    assert quote.price == 85_400.0
    assert quote.is_up is True


async def test_quote_format_helpers() -> None:
    q = _make_quote("HPG", 85_400.0)
    assert q.format_price() == "85,400"
    assert "+1,000" in q.format_change()


async def test_is_ceiling_floor() -> None:
    q = _make_quote("TCB", 90_300.0)  # at ceiling
    assert q.is_ceiling is True
    assert q.is_floor is False
