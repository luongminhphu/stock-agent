"""PersistentOHLCVAdapter: tải lần đầu, sau đó chỉ tải phần đuôi; phát hiện điều chỉnh giá."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.market import candle_store
from src.market.candle_store import PersistentOHLCVAdapter, detect_adjustment
from src.market.ohlcv_service import Candle, Interval, OHLCVAdapter
from src.platform.db import AsyncSessionLocal

_TODAY_CLOSED = date(2026, 11, 10)


class _Source(OHLCVAdapter):
    def __init__(self) -> None:
        self.calls: list[tuple[date, date]] = []
        self.factor = 1.0  # đổi để giả lập nguồn điều chỉnh lịch sử

    async def fetch_candles(self, ticker, from_date, to_date, interval=Interval.D1):  # type: ignore[no-untyped-def]
        self.calls.append((from_date, to_date))
        out, d = [], from_date
        while d <= to_date:
            c = (10_000 + (d - date(2020, 1, 1)).days) * self.factor
            out.append(Candle(ticker, d, c, c, c, c, 100, 0.0))
            d += timedelta(days=1)
        return out


@pytest.fixture(autouse=True)
def _freeze_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(candle_store, "last_closed_session_date", lambda now=None: _TODAY_CLOSED)


@pytest.mark.anyio
async def test_backfill_then_only_tail() -> None:
    src = _Source()
    store = PersistentOHLCVAdapter(src, AsyncSessionLocal)
    start = date(2026, 1, 1)
    first = await store.fetch_candles("aaa", start, date(2026, 11, 1))
    assert first[0].date == start and first[-1].date == date(2026, 11, 1)
    assert len(src.calls) == 1

    again = await store.fetch_candles("AAA", start, date(2026, 11, 1))
    assert len(src.calls) == 1, "đã có trong DB → không gọi nguồn"
    assert [c.close for c in again] == [c.close for c in first]

    await store.fetch_candles("AAA", start, _TODAY_CLOSED)
    tail_from, tail_to = src.calls[-1]
    assert tail_to == _TODAY_CLOSED and (tail_to - tail_from).days <= 20, "chỉ tải phần đuôi"


@pytest.mark.anyio
async def test_adjustment_triggers_full_refetch() -> None:
    src = _Source()
    store = PersistentOHLCVAdapter(src, AsyncSessionLocal)
    start = date(2026, 6, 1)
    await store.fetch_candles("BBB", start, date(2026, 11, 1))
    src.factor = 0.9  # nguồn chia cổ tức → toàn bộ lịch sử bị điều chỉnh
    out = await store.fetch_candles("BBB", start, _TODAY_CLOSED)
    assert src.calls[-1] == (start, _TODAY_CLOSED), "tải lại toàn bộ khi phát hiện điều chỉnh"
    assert out[0].close == pytest.approx((10_000 + (start - date(2020, 1, 1)).days) * 0.9)


def test_detect_adjustment() -> None:
    d = date(2026, 1, 2)
    fresh = [Candle("X", d, 1, 1, 1, 100.2, 1, 0)]
    assert not detect_adjustment({d: 100.0}, fresh)
    assert detect_adjustment({d: 110.0}, fresh)
