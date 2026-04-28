"""VCI (Vietcap) OHLCV adapter — historical daily candles.

Endpoint: https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart
Method: POST  Auth: none.

Payload:  { "timeFrame": "ONE_DAY", "symbols": ["MSR"], "to": <unix_ts>, "countBack": <int> }
Response: [{ "t": [...], "o": [...], "h": [...], "l": [...], "c": [...], "v": [...] }]
  — "t" có thể là unix int, unix string, hoặc ISO string tuỳ phiên bản API.

Owner: market segment.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import httpx

from src.market.ohlcv_service import Candle, Interval, OHLCVAdapter
from src.platform.logging import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://trading.vietcap.com.vn/api/"
_OHLCV_PATH = "chart/OHLCChart/gap-chart"
_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://trading.vietcap.com.vn",
    "Referer": "https://trading.vietcap.com.vn/",
}
_TIMEOUT = 10.0
_INTERVAL_MAP: dict[Interval, str] = {
    Interval.D1: "ONE_DAY",
    Interval.W1: "ONE_WEEK",
    Interval.M1: "ONE_MONTH",
}


def _parse_ts(ts: Any) -> tuple[float, date]:
    """Parse VCI timestamp — str/int/float → (epoch_float, date).

    VCI "t" field có thể là:
      - int/float  : unix epoch  (1745280000)
      - str digits : unix string ("1745280000")
      - str ISO    : "2026-04-22T00:00:00" hoặc "2026-04-22T00:00:00Z"
    """
    if isinstance(ts, (int, float)):
        epoch = float(ts)
    else:
        ts_str = str(ts).strip()
        try:
            epoch = float(ts_str)
        except ValueError:
            epoch = datetime.fromisoformat(
                ts_str.replace("Z", "+00:00")
            ).timestamp()
    return epoch, datetime.fromtimestamp(epoch, tz=timezone.utc).date()


class VCIOHLCVAdapter(OHLCVAdapter):
    """Fetch historical OHLCV candles from Vietcap gap-chart API."""

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
        time_frame = _INTERVAL_MAP.get(interval, "ONE_DAY")
        delta_days = (to_date - from_date).days
        count_back = max(delta_days + 5, 10)
        to_ts = int(
            datetime.combine(to_date, datetime.max.time())
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
        from_epoch = datetime.combine(from_date, datetime.min.time()).replace(
            tzinfo=timezone.utc
        ).timestamp()

        payload: dict[str, Any] = {
            "timeFrame": time_frame,
            "symbols": [ticker.upper()],
            "to": to_ts,
            "countBack": count_back,
        }

        try:
            response = await self._client.post(_OHLCV_PATH, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("vci_ohlcv.http_error", ticker=ticker, status=exc.response.status_code)
            raise
        except httpx.TimeoutException:
            logger.error("vci_ohlcv.timeout", ticker=ticker)
            raise

        raw: list[dict[str, Any]] = response.json()
        if not raw:
            return []

        symbol_data = raw[0]
        timestamps = symbol_data.get("t", [])
        opens      = symbol_data.get("o", [])
        highs      = symbol_data.get("h", [])
        lows       = symbol_data.get("l", [])
        closes     = symbol_data.get("c", [])
        volumes    = symbol_data.get("v", [])

        candles: list[Candle] = []
        for i, raw_ts in enumerate(timestamps):
            try:
                epoch, candle_date = _parse_ts(raw_ts)
                if epoch < from_epoch:
                    continue
                candles.append(Candle(
                    ticker=ticker,
                    date=candle_date,
                    open=float(opens[i]),
                    high=float(highs[i]),
                    low=float(lows[i]),
                    close=float(closes[i]),
                    volume=int(volumes[i]),
                    value=0.0,
                ))
            except (IndexError, ValueError, TypeError) as exc:
                logger.warning("vci_ohlcv.parse_error", ticker=ticker, index=i, error=str(exc))

        return candles

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> VCIOHLCVAdapter:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
