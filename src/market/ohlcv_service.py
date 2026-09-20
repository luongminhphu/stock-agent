"""OHLCV (candlestick) history service interface.

Owner: market segment.

Wave B1: thêm cache in-process cho candles. Candle của phiên đã đóng là bất biến,
nên cache tới phiên giao dịch kế tiếp; dải ngày còn chứa phiên đang chạy chỉ cache
TTL ngắn. Mọi consumer (trend_engine, rrg, why, api) hưởng lợi mà không đổi API.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dtime
from enum import StrEnum
from zoneinfo import ZoneInfo

from src.market.trading_calendar import VNTradingCalendar
from src.platform.logging import get_logger

logger = get_logger(__name__)

_ICT = ZoneInfo("Asia/Ho_Chi_Minh")
# Sau giờ này (ICT) candle D1 của phiên hôm nay coi như chốt (ATC 14:45, công bố ~15:00).
_SESSION_FINAL_TIME = dtime(15, 15)
_NEXT_SESSION_OPEN = dtime(9, 0)

# ---------------------------------------------------------------------------
# ICT date helper
# ---------------------------------------------------------------------------


def _now_ict() -> datetime:
    return datetime.now(_ICT)


def _today_ict() -> date:
    """Return today's date in Asia/Ho_Chi_Minh (ICT, UTC+7).

    Using date.today() on a UTC server returns the wrong date after
    17:00 UTC (= midnight ICT), which would set to_date to tomorrow
    before HOSE has published any data for that day.
    """
    return _now_ict().date()


def _next_session_open(now: datetime) -> datetime:
    """Thời điểm mở cửa phiên giao dịch kế tiếp (ICT) sau `now`."""
    d = now.date()
    if now.time() >= _NEXT_SESSION_OPEN or not VNTradingCalendar.is_trading_day(d):
        d += timedelta(days=1)
    for _ in range(30):  # Tết dài nhất ~10 ngày; 30 là guard
        if VNTradingCalendar.is_trading_day(d):
            break
        d += timedelta(days=1)
    return datetime.combine(d, _NEXT_SESSION_OPEN, tzinfo=_ICT)


def candle_cache_ttl(to_date: date, now: datetime | None = None, *, live_ttl: float) -> float:
    """TTL (giây) cho một dải candle kết thúc tại `to_date`.

    - Dải đã đóng (to_date trước hôm nay, hôm nay không phải ngày giao dịch, hoặc đã
      qua giờ chốt phiên) → cache tới lúc mở phiên kế tiếp.
    - Dải còn chứa phiên đang chạy → `live_ttl`.
    """
    now = now or _now_ict()
    today = now.date()
    closed = (
        to_date < today
        or not VNTradingCalendar.is_trading_day(today)
        or now.time() >= _SESSION_FINAL_TIME
    )
    if not closed:
        return live_ttl
    return max(live_ttl, (_next_session_open(now) - now).total_seconds())


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


class Interval(StrEnum):
    D1 = "1D"
    W1 = "1W"
    M1 = "1M"


@dataclass(frozen=True)
class Candle:
    ticker: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    value: float  # VND

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    @property
    def body_pct(self) -> float:
        """Candle body size as % of open price."""
        if self.open == 0:
            return 0.0
        return abs(self.close - self.open) / self.open * 100


# ---------------------------------------------------------------------------
# Adapter contract
# ---------------------------------------------------------------------------


class OHLCVAdapter(ABC):
    @abstractmethod
    async def fetch_candles(
        self,
        ticker: str,
        from_date: date,
        to_date: date,
        interval: Interval = Interval.D1,
    ) -> list[Candle]: ...

    async def close(self) -> None:  # noqa: B027 — no-op mặc định là chủ ý
        """Release any held resources (e.g. httpx.AsyncClient).

        Default is a no-op so adapters with no resources do not need
        to override this method. Matches the pattern in MarketDataAdapter.
        """


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class OHLCVServiceNotConfiguredError(Exception): ...


_CacheKey = tuple[str, date, date, str]


@dataclass
class _CacheEntry:
    candles: list[Candle]
    expires_at: float  # time.monotonic()


@dataclass
class _InFlight:
    """Marker stampede protection — key đang được fetch bởi coroutine khác."""

    event: asyncio.Event = field(default_factory=asyncio.Event)
    result: list[Candle] | None = None
    error: BaseException | None = None


class _CandleCache:
    """Cache in-process, TTL theo từng key, stampede-safe, bounded.

    Không dùng platform.AsyncTTLCache vì TTL ở đây phụ thuộc từng key
    (dải đã đóng vs đang chạy).
    """

    def __init__(self, max_entries: int) -> None:
        self._max = max_entries
        self._store: dict[_CacheKey, _CacheEntry] = {}
        self._in_flight: dict[_CacheKey, _InFlight] = {}
        self.hits = 0
        self.misses = 0

    async def get_or_fetch(
        self,
        key: _CacheKey,
        fetch: Callable[[], Awaitable[list[Candle]]],
        ttl_for: Callable[[list[Candle]], float],
    ) -> list[Candle]:
        entry = self._store.get(key)
        if entry is not None and time.monotonic() < entry.expires_at:
            self.hits += 1
            return entry.candles

        waiting = self._in_flight.get(key)
        if waiting is not None:
            await waiting.event.wait()
            if waiting.error is not None:
                raise waiting.error
            return waiting.result or []

        flight = _InFlight()
        self._in_flight[key] = flight
        self.misses += 1
        try:
            candles = await fetch()
        except BaseException as exc:
            flight.error = exc
            raise
        else:
            if candles:  # không cache kết quả rỗng — provider có thể trả trễ
                self._evict_if_needed()
                self._store[key] = _CacheEntry(
                    candles=candles, expires_at=time.monotonic() + ttl_for(candles)
                )
            flight.result = candles
            return candles
        finally:
            flight.event.set()
            self._in_flight.pop(key, None)

    def _evict_if_needed(self) -> None:
        if len(self._store) < self._max:
            return
        now = time.monotonic()
        for k in [k for k, e in self._store.items() if e.expires_at <= now]:
            del self._store[k]
        while len(self._store) >= self._max:  # FIFO — dict giữ thứ tự chèn
            self._store.pop(next(iter(self._store)))

    def invalidate(self, ticker: str | None = None) -> None:
        if ticker is None:
            self._store.clear()
            return
        for k in [k for k in self._store if k[0] == ticker]:
            del self._store[k]

    def stats(self) -> dict[str, int]:
        now = time.monotonic()
        return {
            "entries": len(self._store),
            "live": sum(1 for e in self._store.values() if now < e.expires_at),
            "hits": self.hits,
            "misses": self.misses,
        }


class OHLCVService:
    """Historical price service với cache candle in-process (Wave B1).

    Args:
        adapter:      nguồn dữ liệu; None → raise OHLCVServiceNotConfiguredError khi gọi.
        live_ttl:     TTL (giây) cho dải còn chứa phiên đang chạy. Mặc định 300s.
        cache_size:   số key tối đa; 0 → tắt cache (dùng trong test adapter).
    """

    def __init__(
        self,
        adapter: OHLCVAdapter | None = None,
        *,
        live_ttl: float = 300.0,
        cache_size: int = 512,
    ) -> None:
        self._adapter = adapter
        self._live_ttl = live_ttl
        self._cache: _CandleCache | None = _CandleCache(cache_size) if cache_size > 0 else None

    def invalidate_cache(self, ticker: str | None = None) -> None:
        """Xoá cache 1 mã hoặc toàn bộ — dùng khi provider điều chỉnh dữ liệu."""
        if self._cache is not None:
            self._cache.invalidate(ticker.upper() if ticker else None)

    def cache_stats(self) -> dict[str, int]:
        return self._cache.stats() if self._cache is not None else {}

    def _require_adapter(self) -> OHLCVAdapter:
        if self._adapter is None:
            raise OHLCVServiceNotConfiguredError(
                "No OHLCV adapter configured. Wire an adapter in Wave 2."
            )
        return self._adapter

    async def close(self) -> None:
        """Release adapter resources (e.g. httpx connection pool).

        Delegates to adapter.close(). Safe to call even if no adapter
        is configured.
        """
        if self._adapter is not None:
            await self._adapter.close()

    async def get_candles(
        self,
        ticker: str,
        from_date: date,
        to_date: date,
        interval: Interval = Interval.D1,
    ) -> list[Candle]:
        adapter = self._require_adapter()
        symbol = ticker.upper()
        if self._cache is None:
            return await adapter.fetch_candles(symbol, from_date, to_date, interval)

        key: _CacheKey = (symbol, from_date, to_date, str(interval))
        return await self._cache.get_or_fetch(
            key,
            fetch=lambda: adapter.fetch_candles(symbol, from_date, to_date, interval),
            ttl_for=lambda _c: candle_cache_ttl(to_date, live_ttl=self._live_ttl),
        )

    async def get_latest_candles(
        self,
        ticker: str,
        n: int = 20,
        interval: Interval = Interval.D1,
    ) -> list[Candle]:
        """Convenience: fetch last N candles ending at today (ICT)."""
        today = _today_ict()  # ICT-aware, never overshoots into tomorrow
        from_date = today - timedelta(days=n * 2)  # buffer for weekends/holidays
        candles = await self.get_candles(ticker, from_date, today, interval)
        return candles[-n:] if len(candles) >= n else candles
