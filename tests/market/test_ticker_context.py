"""Tests cho src/market/ticker_context.py (Wave B2)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.market.ohlcv_service import Candle, Interval, OHLCVServiceNotConfiguredError
from src.market.quote_service import Quote
from src.market.ticker_context import (
    TickerContextService,
    build_context,
    classify_trend,
    quote_quality,
)

_ICT = ZoneInfo("Asia/Ho_Chi_Minh")
_NOW = datetime(2026, 9, 21, 10, 0, tzinfo=_ICT)  # Thứ 2, trong phiên


def _quote(ticker: str = "VNM", price: float = 70_000.0, ts: datetime | None = None) -> Quote:
    return Quote(
        ticker=ticker,
        price=price,
        change=500.0,
        change_pct=0.72,
        volume=1_200_000,
        value=price * 1_200_000,
        open=69_500.0,
        high=70_500.0,
        low=69_000.0,
        ref_price=69_500.0,
        ceiling=74_350.0,
        floor=64_650.0,
        timestamp=ts or _NOW,
    )


def _candles(
    n: int, ticker: str = "VNM", start: float = 60_000.0, step: float = 100.0
) -> list[Candle]:
    out: list[Candle] = []
    d = _NOW.date() - timedelta(days=n)
    for i in range(n):
        close = start + step * i
        out.append(
            Candle(
                ticker,
                d + timedelta(days=i),
                close - 50,
                close + 200,
                close - 200,
                close,
                1_000_000,
                close * 1e6,
            )
        )
    return out


class TestPureBuilders:
    def test_classify_trend(self) -> None:
        assert classify_trend(100, 90, 80) == "UP"
        assert classify_trend(70, 80, 90) == "DOWN"
        assert classify_trend(85, 90, 80) == "SIDEWAYS"
        assert classify_trend(100, None, 80) == "UNKNOWN"

    def test_quote_quality_live_vs_stale(self) -> None:
        assert quote_quality(_quote(ts=_NOW), _NOW) == "live"
        yesterday = _NOW - timedelta(days=1)
        assert quote_quality(_quote(ts=yesterday), _NOW) == "stale"
        # naive timestamp coi là UTC
        naive_today = _NOW.astimezone(UTC).replace(tzinfo=None)
        assert quote_quality(_quote(ts=naive_today), _NOW) == "live"

    def test_build_context_fallback_when_no_candles(self) -> None:
        ctx = build_context(_quote(), None, now=_NOW)
        assert ctx.source_quality == "fallback"
        assert not ctx.has_indicators
        assert ctx.trend_state == "UNKNOWN"
        assert ctx.price == 70_000.0 and ctx.change_pct == 0.72
        assert "n/a" in ctx.format_for_prompt()

    def test_build_context_fallback_when_too_few_candles(self) -> None:
        ctx = build_context(_quote(), _candles(10), now=_NOW)
        assert ctx.source_quality == "fallback" and ctx.bars == 0

    def test_build_context_full_uptrend(self) -> None:
        candles = _candles(300)  # tăng đều → price > MA20 > MA50
        ctx = build_context(
            _quote(price=90_000.0), candles, now=_NOW
        )  # MA20≈88,950 < 90,000 < hi 90,100
        assert ctx.source_quality == "live"
        assert ctx.bars == 300
        assert ctx.ma20 is not None and ctx.ma50 is not None and ctx.ma20 > ctx.ma50
        assert ctx.trend_state == "UP"
        assert ctx.rsi14 == 100.0  # chỉ có gain
        assert ctx.atr14 is not None and ctx.atr14 > 0
        assert ctx.vol_ratio_20 == pytest.approx(1.0)
        assert ctx.hi_52w == pytest.approx(60_000.0 + 100 * 299 + 200)
        assert ctx.lo_52w == pytest.approx(60_000.0 + 100 * 50 - 200)  # 250 bar cuối
        assert ctx.dist_to_ma20_pct is not None and ctx.dist_to_ma20_pct > 0
        assert ctx.dist_to_hi_52w_pct is not None and ctx.dist_to_hi_52w_pct < 0
        line = ctx.format_for_prompt()
        assert line.startswith("VNM 90,000 (+0.72%)") and "trend UP" in line

    def test_build_context_unsorted_candles_are_sorted(self) -> None:
        candles = list(reversed(_candles(60)))
        ctx = build_context(_quote(), candles, now=_NOW)
        assert ctx.ma20 == pytest.approx(sum(60_000.0 + 100 * i for i in range(40, 60)) / 20)


class _Quotes:
    def __init__(self, *, bulk_fail: bool = False, missing: set[str] | None = None) -> None:
        self.bulk_fail = bulk_fail
        self.missing = missing or set()
        self.bulk_calls = 0
        self.single_calls: list[str] = []

    async def get_bulk_quotes(self, tickers: list[str]) -> list[Quote]:
        self.bulk_calls += 1
        if self.bulk_fail:
            raise RuntimeError("bulk down")
        return [_quote(t, ts=datetime.now(UTC)) for t in tickers if t not in self.missing]

    async def get_quote(self, ticker: str) -> Quote:
        self.single_calls.append(ticker)
        if ticker in self.missing:
            raise LookupError(ticker)
        return _quote(ticker, ts=datetime.now(UTC))


class _Candles:
    def __init__(self, *, fail: set[str] | None = None, not_configured: bool = False) -> None:
        self.fail = fail or set()
        self.not_configured = not_configured
        self.calls: list[tuple[str, date, date]] = []

    async def get_candles(
        self, ticker: str, from_date: date, to_date: date, interval: Interval = Interval.D1
    ) -> list[Candle]:
        self.calls.append((ticker, from_date, to_date))
        if self.not_configured:
            raise OHLCVServiceNotConfiguredError()
        if ticker in self.fail:
            raise RuntimeError("provider down")
        return _candles(120, ticker)


class TestService:
    async def test_get_many_one_bulk_quote_and_one_ohlcv_per_ticker(self) -> None:
        q, c = _Quotes(), _Candles()
        svc = TickerContextService(q, c)
        result = await svc.get_many(["vnm", "HPG", "vnm"])
        assert set(result) == {"VNM", "HPG"}
        assert q.bulk_calls == 1 and q.single_calls == []
        assert sorted(t for t, _, _ in c.calls) == ["HPG", "VNM"]
        _, from_d, to_d = c.calls[0]
        assert (to_d - from_d).days == 365
        assert all(ctx.has_indicators for ctx in result.values())

    async def test_bulk_failure_falls_back_per_ticker(self) -> None:
        q = _Quotes(bulk_fail=True)
        svc = TickerContextService(q, _Candles())
        result = await svc.get_many(["VNM", "HPG"])
        assert set(result) == {"HPG", "VNM"} and q.single_calls == ["HPG", "VNM"]

    async def test_missing_quote_is_omitted_not_raised(self) -> None:
        q = _Quotes(missing={"XXX"})
        result = await TickerContextService(q, _Candles()).get_many(["VNM", "XXX"])
        assert set(result) == {"VNM"} and q.single_calls == ["XXX"]

    async def test_ohlcv_failure_degrades_to_fallback_context(self) -> None:
        svc = TickerContextService(_Quotes(), _Candles(fail={"HPG"}))
        result = await svc.get_many(["VNM", "HPG"])
        assert result["VNM"].source_quality == "live"
        assert result["HPG"].source_quality == "fallback" and result["HPG"].price == 70_000.0

    async def test_no_ohlcv_service_or_not_configured(self) -> None:
        r1 = await TickerContextService(_Quotes(), None).get_many(["VNM"])
        r2 = await TickerContextService(_Quotes(), _Candles(not_configured=True)).get_many(["VNM"])
        assert r1["VNM"].source_quality == "fallback" and r2["VNM"].source_quality == "fallback"

    async def test_get_single_and_lookup_error(self) -> None:
        svc = TickerContextService(_Quotes(missing={"XXX"}), _Candles())
        ctx = await svc.get("vnm")
        assert ctx.ticker == "VNM"
        with pytest.raises(LookupError):
            await svc.get("XXX")

    async def test_empty_input(self) -> None:
        assert await TickerContextService(_Quotes(), _Candles()).get_many([]) == {}
