"""Market breadth aggregation service.

Owner: market segment.
Aggregates bulk quotes from the symbol registry into advance/decline/unchanged
counts for a given exchange (or the full registry).

Design notes:
- Uses QuoteService.get_bulk_quotes() — adapter must implement fetch_bulk_quotes().
- If the adapter raises (not configured / 502), the exception propagates to the
  API route which maps it to an appropriate HTTP error.
- No caching here — callers (API route) should add HTTP cache headers or a
  short-lived in-memory TTL if rate-limiting becomes an issue.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.market.registry import Exchange, registry
from src.market.quote_service import QuoteService


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


class BreadthService:
    """Compute market breadth for a given exchange or the full registry.

    Segment owner: market.
    Injected into the API route via deps.get_breadth_service().
    """

    def __init__(self, quote_svc: QuoteService) -> None:
        self._quote_svc = quote_svc

    async def get_breadth(self, exchange: Exchange | None = None) -> MarketBreadth:
        """Return a MarketBreadth snapshot.

        Args:
            exchange: Filter to a specific exchange.  None = all tickers in registry.

        Raises:
            QuoteServiceNotConfiguredError: if no adapter is wired.
            Exception: propagated from the underlying adapter on network/API error.
        """
        if exchange is not None:
            symbols = registry.list_by_exchange(exchange)
        else:
            symbols = registry.list_all()

        tickers = [s.ticker for s in symbols]
        if not tickers:
            return MarketBreadth(
                exchange=exchange.value if exchange else "ALL",
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
            exchange=exchange.value if exchange else "ALL",
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
