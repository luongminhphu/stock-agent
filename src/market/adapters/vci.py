"""VCI (Vietcap) market data adapter — PRIMARY.

Endpoint: https://trading.vietcap.com.vn/api/
Auth: none required.
Method: POST price/symbols/getList

Response shape (per symbol):
    {
      "listingInfo": {
        "symbol": "HPG",
        "organName": "...",
        "ceiling": 34500, "floor": 30300, "refPrice": 32400,
        "board": "HOSE"
      },
      "matchPrice": {
        "matchPrice": 33100, "priceChange": 700, "priceChangePercent": 2.16,
        "matchVolume": 12000000, "matchValue": ...,
        "open": 32600, "highest": 33200, "lowest": 32400,
        "time": "2025-04-18T09:15:00"
      },
      "bidAsk": { ... }
    }

NOTE: matchVolume từ VCI là đơn vị LÔ (1 lô = 100 CP với HOSE/HNX/UPCoM).
Phải nhân 100 để ra số CP thực tế — Quote.volume contract là số CP.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import httpx

from src.market.quote_service import MarketDataAdapter, Quote
from src.platform.logging import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://trading.vietcap.com.vn/api/"
_PRICE_LIST_PATH = "price/symbols/getList"
_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://trading.vietcap.com.vn",
    "Referer": "https://trading.vietcap.com.vn/",
}
_TIMEOUT = 10.0
_BULK_CHUNK_SIZE = 50
_LOT_SIZE = 100  # 1 lô = 100 CP (HOSE, HNX, UPCoM)


def _safe_float(val: Any, fallback: float = 0.0) -> float:
    """Parse float an toàn — trả fallback nếu None, '', hoặc không parse được."""
    if val is None:
        return fallback
    try:
        return float(val)
    except (ValueError, TypeError):
        return fallback


class VCIAdapter(MarketDataAdapter):
    """Fetch real-time quotes from Vietcap (VCI) price board API."""

    def __init__(self, timeout: float = _TIMEOUT) -> None:
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            headers=_HEADERS,
            timeout=timeout,
        )

    async def fetch_quote(self, ticker: str) -> Quote:
        results = await self.fetch_bulk_quotes([ticker])
        if not results:
            raise ValueError(f"VCI returned no data for ticker '{ticker}'.")
        return results[0]

    async def fetch_bulk_quotes(self, tickers: list[str]) -> list[Quote]:
        """Fetch quotes — all chunks fired in parallel via asyncio.gather.

        Parallel strategy giảm wall-clock từ O(n_chunks * timeout) xuống
        ~1 round-trip. VCI không có strict rate-limit nên an toàn.
        """
        chunks = [
            tickers[i : i + _BULK_CHUNK_SIZE] for i in range(0, len(tickers), _BULK_CHUNK_SIZE)
        ]
        raw_lists = await asyncio.gather(
            *[self._fetch_price_board(chunk) for chunk in chunks]
        )
        results: list[Quote] = []
        for raw in raw_lists:
            results.extend(_parse_price_board(raw))
        return results

    async def _fetch_price_board(self, symbols: list[str]) -> list[dict[str, Any]]:
        try:
            response = await self._client.post(
                _PRICE_LIST_PATH,
                json={"symbols": symbols},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "vci.http_error",
                status=exc.response.status_code,
                symbols=symbols,
            )
            raise
        except httpx.TimeoutException:
            logger.error("vci.timeout", symbols=symbols)
            raise

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> VCIAdapter:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()


def _parse_price_board(data: list[dict[str, Any]]) -> list[Quote]:
    quotes: list[Quote] = []
    meta: list[dict[str, str]] = []
    for item in data:
        try:
            quotes.append(_parse_item(item))
            li = item.get("listingInfo", {})
            ticker = li.get("symbol", "")
            name = li.get("organName", "")
            board = li.get("board", "")  # "HOSE", "HNX", "UPCOM"
            if ticker and name:
                meta.append({"ticker": ticker, "name": name, "board": board})
        except (KeyError, TypeError, ValueError) as exc:
            symbol = item.get("listingInfo", {}).get("symbol", "?")
            logger.warning("vci.parse_error", symbol=symbol, error=str(exc))
    # Fire-and-forget enrich registry with company names from VCI listing data
    if meta:
        _enrich_registry(meta)
    return quotes


def _enrich_registry(meta: list[dict[str, str]]) -> None:
    """Push VCI company name + exchange into SymbolRegistry (best-effort).

    VCI listingInfo provides organName and board — enough to register unknown
    tickers with a proper name and exchange. Sector is left unchanged (only
    static seed has key_metrics + sector; VCI does not expose industry code).
    Called synchronously after every price board parse — no await needed.
    """
    try:
        from src.market.registry import registry
        from src.market.registry_types import Exchange

        _board_map = {"HOSE": Exchange.HOSE, "HNX": Exchange.HNX, "UPCOM": Exchange.UPCOM}
        for item in meta:
            ticker = item["ticker"].upper()
            name = item["name"].strip() or ticker
            exchange = _board_map.get(item["board"].upper(), Exchange.HOSE)
            existing = registry.get(ticker)
            # Only enrich name/exchange — never override sector or key_metrics from static seed
            if existing is None or existing.name == ticker:  # placeholder name → update
                registry.enrich(ticker, name=name, exchange=exchange)
    except Exception:  # noqa: BLE001
        pass  # registry enrich is best-effort — never break price fetch


def _parse_item(item: dict[str, Any]) -> Quote:
    listing = item["listingInfo"]
    match = item["matchPrice"]

    ticker = listing["symbol"]
    ref_price = float(listing["refPrice"])

    # fix: matchPrice=0 means no trade yet (pre-open, halted, low-liquidity).
    # The old expression `match.get("matchPrice") or match.get("refPrice")` treated
    # 0 as falsy and fell through to match.get("refPrice") which is None
    # (refPrice lives in listingInfo, not matchPrice) → _safe_float(None) = 0.0.
    # Correct fallback is listing["refPrice"] which we already have as ref_price.
    raw_price = match.get("matchPrice")
    price = _safe_float(raw_price, fallback=ref_price) if raw_price else ref_price

    change = _safe_float(match.get("priceChange"), fallback=price - ref_price)
    change_pct = _safe_float(
        match.get("priceChangePercent"),
        fallback=(change / ref_price * 100) if ref_price else 0.0,
    )

    # matchVolume từ VCI là đơn vị lô → nhân _LOT_SIZE để ra số CP thực tế
    volume = int(_safe_float(match.get("matchVolume"), fallback=0.0)) * _LOT_SIZE
    value = _safe_float(match.get("matchValue"))
    open_ = _safe_float(match.get("open") or listing.get("refPrice"), fallback=price)
    high = _safe_float(match.get("highest"), fallback=price)
    low = _safe_float(match.get("lowest"), fallback=price)
    ceiling = float(listing["ceiling"])
    floor_ = float(listing["floor"])

    raw_time = match.get("time")
    try:
        timestamp = datetime.fromisoformat(raw_time) if raw_time else datetime.utcnow()
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
