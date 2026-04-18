"""Quote service interface + domain types.

Owner: market segment.
Adapters (VNDIRECT, SSI, FireAnt, mock) implement MarketDataAdapter
and are injected into QuoteService. Wave 2 adds real adapter.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Quote:
    ticker: str
    price: float              # VND
    change: float             # absolute change
    change_pct: float         # percentage change
    volume: int               # shares traded
    value: float              # VND traded
    open: float
    high: float
    low: float
    ref_price: float          # reference (ceiling/floor base)
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


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class QuoteServiceNotConfiguredError(Exception):
    """Raised when QuoteService is called without an adapter (Wave 1 stub)."""


class QuoteService:
    """Public quote service used by watchlist, briefing, bot, and api segments.

    Inject a MarketDataAdapter at startup (Wave 2).
    In Wave 1, all calls raise QuoteServiceNotConfiguredError.
    """

    def __init__(self, adapter: MarketDataAdapter | None = None) -> None:
        self._adapter = adapter

    def _require_adapter(self) -> MarketDataAdapter:
        if self._adapter is None:
            raise QuoteServiceNotConfiguredError(
                "No market data adapter configured. Wire an adapter in Wave 2."
            )
        return self._adapter

    async def get_quote(self, ticker: str) -> Quote:
        return await self._require_adapter().fetch_quote(ticker.upper())

    async def get_bulk_quotes(self, tickers: list[str]) -> list[Quote]:
        return await self._require_adapter().fetch_bulk_quotes(
            [t.upper() for t in tickers]
        )
