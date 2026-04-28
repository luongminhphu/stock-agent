"""VCI (Vietcap) OHLCV adapter — historical daily candles.

Endpoint: https://trading.vietcap.com.vn/api/price/symbols/getHistoricalQuotes
Method: POST
Auth: none required.

Request body:
    {
      "symbol": "MSR",
      "startDate": "2026-04-01",
      "endDate": "2026-04-28",
      "offset": 0,
      "limit": 20,
      "ascending": true
    }

Response shape (per item in data.items):
    {
      "date": "2026-04-22T00:00:00",
      "open": 38500, "high": 39200, "low": 38100,
      "close": 38800, "volume": 1234567, "value": 47891234567
    }

Owner: market segment.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx

from src.market.ohlcv_service import Candle, Interval, OHLCVAdapter
from src.platform.logging import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://trading.vietcap.com.vn/api/"
_OHLCV_PATH = "price/symbols/getHistoricalQuotes"
_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://trading.vietcap.com.vn",
    "Referer": "https://trading.vietcap.com.vn/",
}
_TIMEOUT = 10.0
_MAX_CANDLES = 100


class VCIOHLCVAdapter(OHLCVAdapter):
    """Fetch historical OHLCV candles from Vietcap historical API."""

    def __init__(self, timeout: float = _TIMEOUT) -> None:
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            headers=_HEADERS,
            timeout=timeout,
        )

    async def fetch_candles(
        self,
        ticker: str,
        from_date: date,
        to_date: date,
        interval: Interval = Interval.D1,
    ) -> list[Candle]:
        payload: dict[str, Any] = {
            "symbol": ticker.upper(),
            "startDate": from_date.strftime("%Y-%m-%d"),
            "endDate": to_date.strftime("%Y-%m-%d"),
            "offset": 0,
            "limit": _MAX_CANDLES,
            "ascending": True,
        }
        try:
            response = await self._client.post(_OHLCV_PATH, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "vci_ohlcv.http_error",
                ticker=ticker,
                status=exc.response.status_code,
            )
            raise
        except httpx.TimeoutException:
            logger.error("vci_ohlcv.timeout", ticker=ticker)
            raise

        raw: dict[str, Any] = response.json()
        items: list[dict[str, Any]] = raw.get("data", {}).get("items", [])

        candles: list[Candle] = []
        for item in items:
            try:
                candles.append(_parse_candle(ticker, item))
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("vci_ohlcv.parse_error", ticker=ticker, error=str(exc))
        return candles

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> VCIOHLCVAdapter:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()


def _parse_candle(ticker: str, item: dict[str, Any]) -> Candle:
    raw_date = item["date"]
    candle_date = datetime.fromisoformat(raw_date).date() if isinstance(raw_date, str) else raw_date
    return Candle(
        ticker=ticker,
        date=candle_date,
        open=float(item["open"]),
        high=float(item["high"]),
        low=float(item["low"]),
        close=float(item["close"]),
        volume=int(item["volume"]),
        value=float(item.get("value", 0)),
    )
