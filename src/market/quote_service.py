"""Quote service interface + domain types.

Owner: market segment.
Adapters (VNDIRECT, SSI, FireAnt, mock) implement MarketDataAdapter
and are injected into QuoteService. Wave 2 adds real adapter.
"""

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Quote:
    ticker: str
    price: float  # VND
    change: float  # absolute change
    change_pct: float  # percentage change
    volume: int  # shares traded
    value: float  # VND traded
    open: float
    high: float
    low: float
    ref_price: float  # reference (ceiling/floor base)
    ceiling: float
    floor: float
    timestamp: datetime

    @property
    def is_ceiling(self) -> bool:
        return self.price >= self.ceiling

    @property
    def is_floor(self) -> bool:
        return self.price <= self.floor

    @property
    def is_up(self) -> bool:
        return self.change > 0

    @property
    def is_down(self) -> bool:
        return self.change < 0

    def format_price(self) -> str:
        """Human-readable price string, e.g. '85,400'"""
        return f"{self.price:,.0f}"

    def format_change(self) -> str:
        sign = "+" if self.change >= 0 else ""
        return f"{sign}{self.change:,.0f} ({sign}{self.change_pct:.2f}%)"


# ---------------------------------------------------------------------------
# Adapter contract
# ---------------------------------------------------------------------------


class MarketDataAdapter(ABC):
    """Port that concrete market data providers must implement.

    Do NOT import this from outside the market segment.
    """

    @abstractmethod
    async def fetch_quote(self, ticker: str) -> Quote: ...

    @abstractmethod
    async def fetch_bulk_quotes(self, tickers: list[str]) -> list[Quote]: ...

    async def close(self) -> None:
        """Release any held resources (e.g. httpx.AsyncClient).

        Default is a no-op so adapters with no resources (MockAdapter)
        do not need to override this method.
        Called by bootstrap.shutdown() on application teardown.
        """


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# In-process quote cache with in-flight deduplication
# ---------------------------------------------------------------------------

_QUOTE_TTL    = 3.0   # seconds — short enough for real-time feel, long enough to coalesce
_BULK_TTL     = 3.0   # same for bulk


class _QuoteCache:
    """Thread-safe (asyncio-safe) quote cache.

    Two mechanisms:
    1. TTL cache: once a ticker is fetched, callers within TTL get the cached Quote.
    2. In-flight deduplication: if a fetch for ticker T is already in progress,
       subsequent callers await the same Future instead of starting a new request.

    This prevents the thundering-herd pattern where the dashboard loads
    10 widgets simultaneously, each calling fetch_quote/fetch_bulk_quotes
    for overlapping ticker sets, causing 10x VCI HTTP requests in <100ms.
    """

    def __init__(self) -> None:
        # ticker -> (Quote, expires_at)
        self._single: dict[str, tuple[Quote, float]] = {}
        # cache key -> (list[Quote], expires_at)
        self._bulk: dict[str, tuple[list[Quote], float]] = {}
        # in-flight singles: ticker -> Future[Quote]
        self._inflight_single: dict[str, asyncio.Future[Quote]] = {}
        # in-flight bulk: cache_key -> Future[list[Quote]]
        self._inflight_bulk: dict[str, asyncio.Future[list[Quote]]] = {}

    @staticmethod
    def _bulk_key(tickers: list[str]) -> str:
        return ",".join(sorted(tickers))

    def get_single(self, ticker: str) -> Quote | None:
        entry = self._single.get(ticker)
        if entry and time.monotonic() < entry[1]:
            return entry[0]
        self._single.pop(ticker, None)
        return None

    def set_single(self, ticker: str, quote: Quote) -> None:
        self._single[ticker] = (quote, time.monotonic() + _QUOTE_TTL)

    def get_bulk(self, tickers: list[str]) -> list[Quote] | None:
        key = self._bulk_key(tickers)
        entry = self._bulk.get(key)
        if entry and time.monotonic() < entry[1]:
            return entry[1][0] if False else entry[0]  # mypy workaround
        self._bulk.pop(key, None)
        return None

    def set_bulk(self, tickers: list[str], quotes: list[Quote]) -> None:
        key = self._bulk_key(tickers)
        self._bulk[key] = (quotes, time.monotonic() + _BULK_TTL)
        # Also populate single cache from bulk result
        for q in quotes:
            self._single[q.ticker] = (q, time.monotonic() + _QUOTE_TTL)

    def get_inflight_single(self, ticker: str) -> asyncio.Future[Quote] | None:
        fut = self._inflight_single.get(ticker)
        if fut is not None and not fut.done():
            return fut
        self._inflight_single.pop(ticker, None)
        return None

    def set_inflight_single(self, ticker: str, fut: asyncio.Future[Quote]) -> None:
        self._inflight_single[ticker] = fut

    def clear_inflight_single(self, ticker: str) -> None:
        self._inflight_single.pop(ticker, None)

    def get_inflight_bulk(self, tickers: list[str]) -> asyncio.Future[list[Quote]] | None:
        key = self._bulk_key(tickers)
        fut = self._inflight_bulk.get(key)
        if fut is not None and not fut.done():
            return fut
        self._inflight_bulk.pop(key, None)
        return None

    def set_inflight_bulk(self, tickers: list[str], fut: asyncio.Future[list[Quote]]) -> None:
        self._inflight_bulk[self._bulk_key(tickers)] = fut

    def clear_inflight_bulk(self, tickers: list[str]) -> None:
        self._inflight_bulk.pop(self._bulk_key(tickers), None)


class QuoteServiceNotConfiguredError(Exception):
    """Raised when QuoteService is called without an adapter (Wave 1 stub)."""


class QuoteService:
    """Public quote service used by watchlist, briefing, bot, and api segments.

    Inject a MarketDataAdapter at startup (Wave 2).
    In Wave 1, all calls raise QuoteServiceNotConfiguredError.

    Cache layer (added to prevent VCI/VNDirect 429):
    - TTL 3s: callers within 3s of last successful fetch get cached Quote.
    - In-flight dedup: concurrent callers for same ticker share 1 HTTP request.
    """

    def __init__(self, adapter: MarketDataAdapter | None = None) -> None:
        self._adapter = adapter
        self._cache   = _QuoteCache()

    def _require_adapter(self) -> MarketDataAdapter:
        if self._adapter is None:
            raise QuoteServiceNotConfiguredError(
                "No market data adapter configured. Wire an adapter in Wave 2."
            )
        return self._adapter

    async def get_quote(self, ticker: str) -> Quote:
        sym = ticker.upper()

        # 1. TTL cache hit
        cached = self._cache.get_single(sym)
        if cached is not None:
            return cached

        # 2. In-flight dedup: join existing request if one is running
        inflight = self._cache.get_inflight_single(sym)
        if inflight is not None:
            return await asyncio.shield(inflight)

        # 3. Start new fetch, register as in-flight
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[Quote] = loop.create_future()
        self._cache.set_inflight_single(sym, fut)
        try:
            quote = await self._require_adapter().fetch_quote(sym)
            self._cache.set_single(sym, quote)
            fut.set_result(quote)
            return quote
        except Exception as exc:
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._cache.clear_inflight_single(sym)

    async def get_bulk_quotes(self, tickers: list[str]) -> list[Quote]:
        syms = [t.upper() for t in tickers]

        # 1. TTL cache hit (exact same sorted set)
        cached_bulk = self._cache.get_bulk(syms)
        if cached_bulk is not None:
            return cached_bulk

        # 2. In-flight dedup
        inflight = self._cache.get_inflight_bulk(syms)
        if inflight is not None:
            return await asyncio.shield(inflight)

        # 3. Start new fetch
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[list[Quote]] = loop.create_future()
        self._cache.set_inflight_bulk(syms, fut)
        try:
            quotes = await self._require_adapter().fetch_bulk_quotes(syms)
            self._cache.set_bulk(syms, quotes)
            fut.set_result(quotes)
            return quotes
        except Exception as exc:
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._cache.clear_inflight_bulk(syms)

    async def close(self) -> None:
        """Forward close to the underlying adapter. Safe to call even if no adapter."""
        if self._adapter is not None:
            await self._adapter.close()
