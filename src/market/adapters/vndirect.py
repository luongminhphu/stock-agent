"""VNDirect market data adapter — TERTIARY (giá EOD chính thức).

Endpoint: GET https://finfo-api.vndirect.com.vn/v4/stock_prices
Auth: none.

Lưu ý quan trọng (sửa lỗi cũ):
    - Endpoint cũ `/v4/stocks` là danh mục niêm yết, KHÔNG có giá → adapter cũ
      luôn parse ra rỗng / sai.
    - `/v4/stock_prices` trả giá theo đơn vị NGHÌN ĐỒNG (27.35 = 27.350đ) →
      nhân 1.000 để cùng đơn vị VND với VCI/KBS.
    - Trường `floor` ở endpoint này là TÊN SÀN ("HOSE"), giá sàn là `floorPrice`.
    - Dữ liệu là bản ghi theo ngày (cập nhật trong/ sau phiên), phù hợp làm nguồn
      cuối của chuỗi fallback và đối soát giá đóng cửa.

Query: q=code:HPG · sort=date · size=1 → bản ghi mới nhất của 1 mã.
Response:
    {"data": [{"code": "HPG", "date": "2025-04-18", "time": "15:04:01",
               "basicPrice": 32.4, "ceilingPrice": 34.65, "floorPrice": 30.15,
               "open": 32.6, "high": 33.2, "low": 32.4, "close": 33.1,
               "change": 0.7, "pctChange": 2.16, "nmVolume": 12000000,
               "nmValue": 3.9e11}]}
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import httpx

from src.market.adapters.http_guard import guarded_request
from src.market.quote_service import MarketDataAdapter, Quote
from src.platform.logging import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://finfo-api.vndirect.com.vn/v4/"
_PRICES_PATH = "stock_prices"
_FIELDS = (
    "code,date,time,basicPrice,ceilingPrice,floorPrice,open,high,low,close,"
    "change,pctChange,nmVolume,nmValue"
)
_PRICE_UNIT = 1000.0  # API trả nghìn đồng
_HEADERS = {
    "Accept": "application/json",
    "Origin": "https://www.vndirect.com.vn",
    "Referer": "https://www.vndirect.com.vn/",
}
_TIMEOUT = 10.0
_MAX_CONCURRENT_CHUNKS = 5  # giới hạn concurrent — tránh VNDirect reject


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
        self._sem = asyncio.Semaphore(_MAX_CONCURRENT_CHUNKS)

    async def fetch_quote(self, ticker: str) -> Quote:
        results = await self.fetch_bulk_quotes([ticker])
        if not results:
            raise ValueError(f"VNDirect returned no data for ticker '{ticker}'.")
        return results[0]

    async def fetch_bulk_quotes(self, tickers: list[str]) -> list[Quote]:
        """Mỗi mã một request (size=1, bản ghi mới nhất) — giới hạn đồng thời."""

        async def _one(ticker: str) -> list[dict[str, Any]]:
            async with self._sem:
                return await self._fetch_latest(ticker)

        raw_lists = await asyncio.gather(*[_one(t) for t in tickers])
        results: list[Quote] = []
        for raw in raw_lists:
            results.extend(_parse_stocks(raw))
        return results

    async def _fetch_latest(self, ticker: str) -> list[dict[str, Any]]:
        try:
            response = await guarded_request(
                self._client,
                "GET",
                _PRICES_PATH,
                params={
                    "q": f"code:{ticker.upper()}",
                    "sort": "date",
                    "size": 1,
                    "page": 1,
                    "fields": _FIELDS,
                },
            )
            response.raise_for_status()
            data: list[dict[str, Any]] = response.json().get("data", [])
            return data[:1]
        except httpx.HTTPStatusError as exc:
            logger.error("vndirect.http_error", status=exc.response.status_code, ticker=ticker)
            raise
        except httpx.TimeoutException:
            logger.error("vndirect.timeout", ticker=ticker)
            raise

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> VNDirectAdapter:
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


def _px(item: dict[str, Any], key: str) -> float | None:
    val = item.get(key)
    if val is None or val == "":
        return None
    try:
        return float(val) * _PRICE_UNIT
    except (TypeError, ValueError):
        return None


def _parse_item(item: dict[str, Any]) -> Quote:
    ticker = str(item["code"]).upper()
    ref_price = _px(item, "basicPrice")
    if not ref_price:
        raise ValueError(f"VNDirect: thiếu basicPrice cho {ticker}")
    price = _px(item, "close") or ref_price
    change = _px(item, "change")
    if change is None:
        change = price - ref_price
    change_pct = _safe_float(item.get("pctChange"), fallback=change / ref_price * 100)
    volume = int(_safe_float(item.get("nmVolume")))
    open_ = _px(item, "open") or (price if volume > 0 else ref_price)

    raw = f"{item.get('date', '')} {item.get('time', '')}".strip()
    try:
        timestamp = datetime.fromisoformat(raw) if raw else datetime.utcnow()
    except ValueError:
        timestamp = datetime.utcnow()

    return Quote(
        ticker=ticker,
        price=price,
        change=change,
        change_pct=change_pct,
        volume=volume,
        value=_safe_float(item.get("nmValue")),
        open=open_,
        high=_px(item, "high") or price,
        low=_px(item, "low") or price,
        ref_price=ref_price,
        ceiling=_px(item, "ceilingPrice") or round(ref_price * 1.07, -1),
        floor=_px(item, "floorPrice") or round(ref_price * 0.93, -1),
        timestamp=timestamp,
    )
