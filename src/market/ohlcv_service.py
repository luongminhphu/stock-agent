"""OHLCV (candlestick) history service interface.

Owner: market segment.
Wave 2: implement adapter backed by real data provider.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from enum import Enum


class Interval(str, Enum):
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


class OHLCVAdapter(ABC):
    @abstractmethod
    async def fetch_candles(
        self,
        ticker: str,
        from_date: date,
        to_date: date,
        interval: Interval = Interval.D1,
    ) -> list[Candle]: ...


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
        """Convenience: fetch last N candles. Adapter resolves date range."""
        from datetime import timedelta

        today = date.today()
        # Provide enough buffer for weekends/holidays
        from_date = today - timedelta(days=n * 2)
        candles = await self.get_candles(ticker, from_date, today, interval)
        return candles[-n:] if len(candles) >= n else candles
