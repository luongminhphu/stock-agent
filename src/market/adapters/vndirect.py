"""VNDirect market data adapter — SECONDARY.

Endpoint: https://finfo-api.vndirect.com.vn/v4/
Auth: none required for public quote data.

GET /stocks?q=code:{TICKER}&fields=...
Response shape:
    {
      "data": [{
        "code": "HPG",
        "close": 33100,
        "priceChange": 700,
        "pctPriceChange": 2.16,
        "nmVolume": 12000000,
        "nmValue": ...,
        "open": 32600,
        "high": 33200,
        "low": 32400,
        "refPrice": 32400,
        "ceiling": 34500,
        "floor": 30300,
        "date": "2025-04-18"
      }]
    }
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from src.market.quote_service import MarketDataAdapter, Quote
from src.platform.logging import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://finfo-api.vndirect.com.vn/v4/"
_STOCKS_PATH = "stocks"
_FIELDS = "code,close,priceChange,pctPriceChange,nmVolume,nmValue,open,high,low,refPrice,ceiling,floor,date"
_HEADERS = {
    "Accept": "application/json",
    "Origin": "https://www.vndirect.com.vn",
    "Referer": "https://www.vndirect.com.vn/",
}
_TIMEOUT = 10.0
_BULK_CHUNK_SIZE = 20  # VNDirect query string limit


def _safe_float(val: Any, fallback: float = 0.0) -> float:
    """Parse float an toàn — trả fallback nếu None, '', hoặc không parse được."""
    if val is None:
        return fallback
    try:
        return float(val)
    except (ValueError, TypeError):
        return fallback


class VNDirectAdapter(MarketDataAdapter):
    """Fetch real-time quotes from VNDirect public finfo API."""

    def __init__(self, timeout: float = _TIMEOUT) -> None:
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            headers=_HEADERS,
            timeout=timeout,
        )

    async def fetch_quote(self, ticker: str) -> Quote:
        results = await self.fetch_bulk_quotes([ticker])
        if not results:
            raise ValueError(f"VNDirect returned no data for ticker '{ticker}'.")
        return results[0]

    async def fetch_bulk_quotes(self, tickers: list[str]) -> list[Quote]:
        chunks = [
            tickers[i : i + _BULK_CHUNK_SIZE] for i in range(0, len(tickers), _BULK_CHUNK_SIZE)
        ]
        results: list[Quote] = []
        for chunk in chunks:
            raw = await self._fetch_stocks(chunk)
            results.extend(_parse_stocks(raw))
        return results

    async def _fetch_stocks(self, tickers: list[str]) -> list[dict[str, Any]]:
        query = ",".join(f"code:{t}" for t in tickers)
        try:
            response = await self._client.get(
                _STOCKS_PATH,
                params={
                    "q": query,
                    "fields": _FIELDS,
                    "size": len(tickers),
                },
            )
            response.raise_for_status()
            return response.json().get("data", [])
        except httpx.HTTPStatusError as exc:
            logger.error(
                "vndirect.http_error",
                status=exc.response.status_code,
                tickers=tickers,
            )
            raise
        except httpx.TimeoutException:
            logger.error("vndirect.timeout", tickers=tickers)
            raise

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "VNDirectAdapter":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()


def _parse_stocks(data: list[dict[str, Any]]) -> list[Quote]:
    quotes: list[Quote] = []
    for item in data:
        try:
            quotes.append(_parse_item(item))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("vndirect.parse_error", item=item, error=str(exc))
    return quotes


def _parse_item(item: dict[str, Any]) -> Quote:
    ticker = item["code"]
    ref_price = _safe_float(item.get("refPrice"))
    price = _safe_float(item.get("close"), fallback=ref_price)

    change = _safe_float(item.get("priceChange"), fallback=price - ref_price)
    change_pct = _safe_float(
        item.get("pctPriceChange"),
        fallback=(change / ref_price * 100) if ref_price else 0.0,
    )

    volume = int(_safe_float(item.get("nmVolume")))
    value = _safe_float(item.get("nmValue"))
    open_ = _safe_float(item.get("open"), fallback=price)
    high = _safe_float(item.get("high"), fallback=price)
    low = _safe_float(item.get("low"), fallback=price)
    ceiling = _safe_float(item.get("ceiling"), fallback=price * 1.07)
    floor_ = _safe_float(item.get("floor"), fallback=price * 0.93)

    raw_date = item.get("date")
    try:
        timestamp = datetime.fromisoformat(raw_date) if raw_date else datetime.utcnow()
    except ValueError:
        timestamp = datetime.utcnow()

    return Quote(
        ticker=ticker,
        price=price,
        change=change,
        change_pct=change_pct,
        volume=volume,
        value=value,
        open=open_,
        high=high,
        low=low,
        ref_price=ref_price,
        ceiling=ceiling,
        floor=floor_,
        timestamp=timestamp,
    )
