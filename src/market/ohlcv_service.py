"""OHLCV (candlestick) history service interface.

Owner: market segment.
Wave 2: implement adapter backed by real data provider.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum


# ---------------------------------------------------------------------------
# ICT date helper
# ---------------------------------------------------------------------------

def _today_ict() -> date:
    """Return today's date in Asia/Ho_Chi_Minh (ICT, UTC+7).

    Using date.today() on a UTC server returns the wrong date after
    17:00 UTC (= midnight ICT), which would set to_date to tomorrow
    before HOSE has published any data for that day.

    Falls back to date.today() if zoneinfo is unavailable.
    """
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    except Exception:  # noqa: BLE001
        return date.today()


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

    async def close(self) -> None:
        """Release any held resources (e.g. httpx.AsyncClient).

        Default is a no-op so adapters with no resources do not need
        to override this method. Matches the pattern in MarketDataAdapter.
        """


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class OHLCVServiceNotConfiguredError(Exception): ...


class OHLCVService:
    """Historical price service. Wave 1 stub — requires adapter in Wave 2."""

    def __init__(self, adapter: OHLCVAdapter | None = None) -> None:
        self._adapter = adapter

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
        return await self._require_adapter().fetch_candles(
            ticker.upper(), from_date, to_date, interval
        )

    async def get_latest_candles(
        self,
        ticker: str,
        n: int = 20,
        interval: Interval = Interval.D1,
    ) -> list[Candle]:
        """Convenience: fetch last N candles ending at today (ICT)."""
        from datetime import timedelta

        today = _today_ict()  # ICT-aware, never overshoots into tomorrow
        from_date = today - timedelta(days=n * 2)  # buffer for weekends/holidays
        candles = await self.get_candles(ticker, from_date, today, interval)
        return candles[-n:] if len(candles) >= n else candles
