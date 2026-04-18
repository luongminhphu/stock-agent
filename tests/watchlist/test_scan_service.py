"""Unit tests for ScanService.

QuoteService is replaced by a lightweight stub — no HTTP calls.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest

from src.watchlist.models import AlertConditionType, AlertStatus
from src.watchlist.scan_service import ScanService, ScanServiceNotConfiguredError
from src.watchlist.service import AddAlertInput, AddToWatchlistInput, WatchlistService


@dataclass
class _FakeQuote:
    ticker: str
    price: float
    change: float = 0.0
    change_pct: float = 0.0
    volume: int = 1_000_000
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    ref_price: float = 0.0
    ceiling: float = 0.0
    floor: float = 0.0
    is_ceiling: bool = False
    is_floor: bool = False
    timestamp: datetime = datetime.utcnow()


class _StubQuoteService:
    """Returns pre-configured quotes; raises KeyError for unknown tickers."""

    def __init__(self, quotes: dict[str, _FakeQuote]) -> None:
        self._quotes = quotes

    async def get_quote(self, ticker: str) -> _FakeQuote:
        return self._quotes[ticker]

    async def get_bulk_quotes(self, tickers: list[str]) -> list[_FakeQuote]:
        return [self._quotes[t] for t in tickers if t in self._quotes]


USER = "scan_user"


async def test_scan_raises_without_quote_service(session):
    svc = ScanService(session=session)
    with pytest.raises(ScanServiceNotConfiguredError):
        await svc.scan_user(USER)


async def test_scan_empty_watchlist(session):
    qs = _StubQuoteService({})
    svc = ScanService(session=session, quote_service=qs)
    result = await svc.scan_user(USER)
    assert result.signals == []
    assert result.errors == {}


async def test_scan_no_alerts_quiet_ticker(session):
    """Ticker with <5% move and no alerts should NOT appear in signals."""
    wl_svc = WatchlistService(session)
    await wl_svc.add(AddToWatchlistInput(user_id=USER, ticker="VNM"))
    await session.flush()

    qs = _StubQuoteService({"VNM": _FakeQuote(ticker="VNM", price=80_000, change_pct=1.0)})
    svc = ScanService(session=session, quote_service=qs)
    result = await svc.scan_user(USER)

    assert result.signals == []


async def test_scan_detects_strong_move(session):
    """Ticker with >=5% move and no alert still appears as a signal."""
    wl_svc = WatchlistService(session)
    await wl_svc.add(AddToWatchlistInput(user_id=USER, ticker="HPG"))
    await session.flush()

    qs = _StubQuoteService({"HPG": _FakeQuote(ticker="HPG", price=25_000, change_pct=6.5)})
    svc = ScanService(session=session, quote_service=qs)
    result = await svc.scan_user(USER)

    assert len(result.signals) == 1
    assert result.signals[0].ticker == "HPG"
    assert result.signals[0].signal_type == "strong_move"


async def test_scan_triggers_price_above_alert(session):
    wl_svc = WatchlistService(session)
    await wl_svc.add(AddToWatchlistInput(user_id=USER, ticker="VIC"))
    await session.flush()
    await wl_svc.add_alert(
        AddAlertInput(
            user_id=USER,
            ticker="VIC",
            condition_type=AlertConditionType.PRICE_ABOVE,
            threshold=50_000,
        )
    )
    await session.flush()

    qs = _StubQuoteService({"VIC": _FakeQuote(ticker="VIC", price=55_000, change_pct=2.0)})
    svc = ScanService(session=session, quote_service=qs)
    result = await svc.scan_user(USER)

    assert len(result.triggered_alerts) == 1
    assert result.triggered_alerts[0].ticker == "VIC"
    assert result.triggered_alerts[0].status == AlertStatus.TRIGGERED


async def test_scan_does_not_trigger_below_threshold(session):
    wl_svc = WatchlistService(session)
    await wl_svc.add(AddToWatchlistInput(user_id=USER, ticker="MWG"))
    await session.flush()
    await wl_svc.add_alert(
        AddAlertInput(
            user_id=USER,
            ticker="MWG",
            condition_type=AlertConditionType.PRICE_ABOVE,
            threshold=100_000,
        )
    )
    await session.flush()

    qs = _StubQuoteService({"MWG": _FakeQuote(ticker="MWG", price=80_000, change_pct=0.5)})
    svc = ScanService(session=session, quote_service=qs)
    result = await svc.scan_user(USER)

    assert result.triggered_alerts == []


async def test_scan_for_user_alias(session):
    """scan_for_user() is an alias of scan_user() — must return same result."""
    qs = _StubQuoteService({})
    svc = ScanService(session=session, quote_service=qs)
    r1 = await svc.scan_user(USER)
    r2 = await svc.scan_for_user(USER)
    assert r1.scanned_at is not r2.scanned_at  # different calls
    assert r1.signals == r2.signals == []


async def test_triggered_alert_not_re_triggered(session):
    """Already-triggered alert should not appear again."""
    wl_svc = WatchlistService(session)
    await wl_svc.add(AddToWatchlistInput(user_id=USER, ticker="FPT"))
    await session.flush()
    alert = await wl_svc.add_alert(
        AddAlertInput(
            user_id=USER,
            ticker="FPT",
            condition_type=AlertConditionType.PRICE_BELOW,
            threshold=90_000,
        )
    )
    await session.flush()
    # Pre-trigger the alert
    alert.mark_triggered(price=85_000)
    await session.flush()

    qs = _StubQuoteService({"FPT": _FakeQuote(ticker="FPT", price=85_000, change_pct=-1.0)})
    svc = ScanService(session=session, quote_service=qs)
    result = await svc.scan_user(USER)

    assert result.triggered_alerts == []
