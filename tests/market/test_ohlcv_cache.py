"""Tests cho cache candle in-process của OHLCVService (Wave B1)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.market.ohlcv_service import (
    Candle,
    Interval,
    OHLCVAdapter,
    OHLCVService,
    OHLCVServiceNotConfiguredError,
    candle_cache_ttl,
)

_ICT = ZoneInfo("Asia/Ho_Chi_Minh")


class CountingAdapter(OHLCVAdapter):
    def __init__(self, *, empty_for: set[str] | None = None, delay: float = 0.0) -> None:
        self.calls: list[tuple[str, date, date, str]] = []
        self._empty = empty_for or set()
        self._delay = delay

    async def fetch_candles(
        self, ticker: str, from_date: date, to_date: date, interval: Interval = Interval.D1
    ) -> list[Candle]:
        self.calls.append((ticker, from_date, to_date, str(interval)))
        if self._delay:
            await asyncio.sleep(self._delay)
        if ticker in self._empty:
            return []
        return [
            Candle(ticker, from_date, 10.0, 11.0, 9.0, 10.5, 1000, 10_500.0),
            Candle(ticker, to_date, 10.5, 12.0, 10.0, 11.5, 1200, 13_800.0),
        ]


@pytest.fixture
def adapter() -> CountingAdapter:
    return CountingAdapter()


@pytest.fixture
def svc(adapter: CountingAdapter) -> OHLCVService:
    return OHLCVService(adapter, live_ttl=300.0)


_FROM = date(2026, 8, 1)
_TO = date(2026, 8, 29)


class TestCacheBehaviour:
    async def test_second_call_same_range_hits_cache(self, svc, adapter) -> None:
        a = await svc.get_candles("vnm", _FROM, _TO)
        b = await svc.get_candles("VNM", _FROM, _TO)
        assert a == b
        assert len(adapter.calls) == 1
        assert adapter.calls[0][0] == "VNM"  # upper-cased trước khi tới adapter
        assert svc.cache_stats() == {"entries": 1, "live": 1, "hits": 1, "misses": 1}

    async def test_different_range_or_interval_is_separate_key(self, svc, adapter) -> None:
        await svc.get_candles("VNM", _FROM, _TO)
        await svc.get_candles("VNM", _FROM, _TO - timedelta(days=1))
        await svc.get_candles("VNM", _FROM, _TO, Interval.W1)
        assert len(adapter.calls) == 3

    async def test_empty_result_is_not_cached(self) -> None:
        adapter = CountingAdapter(empty_for={"NEW"})
        svc = OHLCVService(adapter)
        assert await svc.get_candles("NEW", _FROM, _TO) == []
        assert await svc.get_candles("NEW", _FROM, _TO) == []
        assert len(adapter.calls) == 2

    async def test_concurrent_calls_dedupe_to_one_fetch(self) -> None:
        adapter = CountingAdapter(delay=0.05)
        svc = OHLCVService(adapter)
        results = await asyncio.gather(*[svc.get_candles("HPG", _FROM, _TO) for _ in range(5)])
        assert all(r == results[0] for r in results)
        assert len(adapter.calls) == 1

    async def test_invalidate_by_ticker_and_all(self, svc, adapter) -> None:
        await svc.get_candles("VNM", _FROM, _TO)
        await svc.get_candles("HPG", _FROM, _TO)
        svc.invalidate_cache("vnm")
        await svc.get_candles("VNM", _FROM, _TO)
        await svc.get_candles("HPG", _FROM, _TO)  # vẫn hit
        assert len(adapter.calls) == 3
        svc.invalidate_cache()
        await svc.get_candles("HPG", _FROM, _TO)
        assert len(adapter.calls) == 4

    async def test_cache_disabled_when_size_zero(self, adapter) -> None:
        svc = OHLCVService(adapter, cache_size=0)
        await svc.get_candles("VNM", _FROM, _TO)
        await svc.get_candles("VNM", _FROM, _TO)
        assert len(adapter.calls) == 2
        assert svc.cache_stats() == {}

    async def test_bounded_eviction(self, adapter) -> None:
        svc = OHLCVService(adapter, cache_size=3)
        for i in range(5):
            await svc.get_candles(f"T{i}", _FROM, _TO)
        assert svc.cache_stats()["entries"] <= 3

    async def test_no_adapter_still_raises(self) -> None:
        with pytest.raises(OHLCVServiceNotConfiguredError):
            await OHLCVService().get_candles("VNM", _FROM, _TO)

    async def test_adapter_error_propagates_and_is_not_cached(self) -> None:
        class Boom(OHLCVAdapter):
            n = 0

            async def fetch_candles(self, ticker, from_date, to_date, interval=Interval.D1):
                self.n += 1
                raise RuntimeError("provider down")

        boom = Boom()
        svc = OHLCVService(boom)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await svc.get_candles("VNM", _FROM, _TO)
        assert boom.n == 2


class TestCandleCacheTtl:
    """Thứ 2 21/09/2026 là ngày giao dịch; 20/09/2026 là Chủ nhật."""

    def test_intraday_range_uses_live_ttl(self) -> None:
        now = datetime(2026, 9, 21, 10, 30, tzinfo=_ICT)  # Thứ 2, đang giao dịch
        assert candle_cache_ttl(now.date(), now, live_ttl=300.0) == 300.0

    def test_closed_range_caches_until_next_session_open(self) -> None:
        now = datetime(2026, 9, 21, 10, 30, tzinfo=_ICT)
        ttl = candle_cache_ttl(now.date() - timedelta(days=1), now, live_ttl=300.0)
        next_open = datetime(2026, 9, 22, 9, 0, tzinfo=_ICT)
        assert ttl == pytest.approx((next_open - now).total_seconds())

    def test_after_session_final_time_today_is_closed(self) -> None:
        now = datetime(2026, 9, 21, 15, 30, tzinfo=_ICT)
        ttl = candle_cache_ttl(now.date(), now, live_ttl=300.0)
        assert ttl == pytest.approx(
            (datetime(2026, 9, 22, 9, 0, tzinfo=_ICT) - now).total_seconds()
        )

    def test_weekend_skips_to_monday(self) -> None:
        now = datetime(2026, 9, 20, 12, 0, tzinfo=_ICT)  # Chủ nhật
        ttl = candle_cache_ttl(now.date(), now, live_ttl=300.0)
        assert ttl == pytest.approx(
            (datetime(2026, 9, 21, 9, 0, tzinfo=_ICT) - now).total_seconds()
        )

    def test_national_day_holiday_is_skipped(self) -> None:
        # 01/09/2026 (Thứ 3) — ngày lễ 2/9 rơi Thứ 4 02/09/2026
        now = datetime(2026, 9, 1, 16, 0, tzinfo=_ICT)
        ttl = candle_cache_ttl(now.date(), now, live_ttl=300.0)
        assert ttl == pytest.approx((datetime(2026, 9, 3, 9, 0, tzinfo=_ICT) - now).total_seconds())
