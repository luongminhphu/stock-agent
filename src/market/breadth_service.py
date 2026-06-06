"""Market breadth aggregation service.

Owner: market segment.
Aggregates bulk quotes from the symbol registry into advance/decline/unchanged
counts for a given exchange (or the full registry).

Design notes:
- Uses QuoteService.get_bulk_quotes() — adapter must implement fetch_bulk_quotes().
- If the adapter raises (not configured / 502), the exception propagates to the
  API route which maps it to an appropriate HTTP error.
- TTL cache (default 30s) per exchange key with asyncio.Lock per key to prevent
  thundering herd: concurrent requests wait on the in-flight fetch rather than
  each firing their own full-exchange bulk quote call.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from src.market.registry import Exchange, registry
from src.market.quote_service import QuoteService

_DEFAULT_TTL = 30.0  # seconds — breadth does not need sub-30s freshness


@dataclass(frozen=True)
class MarketBreadth:
    """Aggregate breadth snapshot for a given exchange scope."""

    exchange: str  # "HOSE" | "HNX" | "UPCOM" | "ALL"
    advance: int        # change > 0
    decline: int        # change < 0
    unchanged: int      # change == 0
    ceiling: int        # price >= ceiling price (trần)
    floor: int          # price <= floor price (sàn)
    total: int          # number of tickers with valid quotes
    advance_pct: float  # advance / total * 100
    decline_pct: float  # decline / total * 100
    unchanged_pct: float


@dataclass
class _CacheEntry:
    value: MarketBreadth
    expires_at: float  # monotonic timestamp


class BreadthService:
    """Compute market breadth for a given exchange or the full registry.

    Segment owner: market.
    Injected into the API route via deps.get_breadth_service().

    TTL cache:
        Per-exchange key, default TTL 30s. asyncio.Lock per key prevents
        thundering herd — concurrent callers wait on the in-flight fetch
        instead of each firing a full bulk-quote call.
    """

    def __init__(self, quote_svc: QuoteService, ttl: float = _DEFAULT_TTL) -> None:
        self._quote_svc = quote_svc
        self._ttl = ttl
        self._cache: dict[str, _CacheEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def get_breadth(self, exchange: Exchange | None = None) -> MarketBreadth:
        """Return a MarketBreadth snapshot, served from cache when fresh.

        Args:
            exchange: Filter to a specific exchange.  None = all tickers in registry.

        Raises:
            QuoteServiceNotConfiguredError: if no adapter is wired.
            Exception: propagated from the underlying adapter on network/API error.
        """
        key = exchange.value if exchange else "ALL"

        # Fast path: cache hit (no lock needed for read)
        entry = self._cache.get(key)
        if entry is not None and time.monotonic() < entry.expires_at:
            return entry.value

        # Slow path: acquire per-key lock so only 1 coroutine fetches at a time.
        # Others wait, then get the freshly populated cache on the fast path.
        async with self._lock_for(key):
            # Re-check after acquiring lock — another coroutine may have just filled it.
            entry = self._cache.get(key)
            if entry is not None and time.monotonic() < entry.expires_at:
                return entry.value

            result = await self._fetch(exchange, key)
            self._cache[key] = _CacheEntry(
                value=result,
                expires_at=time.monotonic() + self._ttl,
            )
            return result

    async def _fetch(self, exchange: Exchange | None, key: str) -> MarketBreadth:
        """Unconditional fetch from adapter — no cache logic here."""
        if exchange is not None:
            symbols = registry.list_by_exchange(exchange)
        else:
            symbols = registry.list_all()

        tickers = [s.ticker for s in symbols]
        if not tickers:
            return MarketBreadth(
                exchange=key,
                advance=0, decline=0, unchanged=0,
                ceiling=0, floor=0, total=0,
                advance_pct=0.0, decline_pct=0.0, unchanged_pct=0.0,
            )

        quotes = await self._quote_svc.get_bulk_quotes(tickers)

        advance = sum(1 for q in quotes if q.change > 0)
        decline = sum(1 for q in quotes if q.change < 0)
        unchanged = sum(1 for q in quotes if q.change == 0)
        ceiling = sum(1 for q in quotes if q.is_ceiling)
        floor_count = sum(1 for q in quotes if q.is_floor)
        total = len(quotes)

        def pct(n: int) -> float:
            return round(n / total * 100, 1) if total else 0.0

        return MarketBreadth(
            exchange=key,
            advance=advance,
            decline=decline,
            unchanged=unchanged,
            ceiling=ceiling,
            floor=floor_count,
            total=total,
            advance_pct=pct(advance),
            decline_pct=pct(decline),
            unchanged_pct=pct(unchanged),
        )
