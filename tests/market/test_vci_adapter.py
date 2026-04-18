"""Unit tests for VCIAdapter — no real HTTP, uses httpx mock transport."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.market.adapters.vci import VCIAdapter, _parse_item
from src.market.quote_service import Quote


def _vci_item(
    symbol: str = "HPG",
    price: float = 33100,
    change: float = 700,
    change_pct: float = 2.16,
    volume: int = 12_000_000,
) -> dict:
    return {
        "listingInfo": {
            "symbol": symbol,
            "refPrice": price - change,
            "ceiling": round((price - change) * 1.07, -2),
            "floor": round((price - change) * 0.93, -2),
            "board": "HOSE",
        },
        "matchPrice": {
            "matchPrice": price,
            "priceChange": change,
            "priceChangePercent": change_pct,
            "matchVolume": volume,
            "matchValue": price * volume,
            "open": price - change,
            "highest": price + 100,
            "lowest": price - 200,
            "time": "2025-04-18T09:15:00",
        },
        "bidAsk": {},
    }


def test_parse_item_basic() -> None:
    item = _vci_item("HPG", price=33100, change=700)
    quote = _parse_item(item)
    assert quote.ticker == "HPG"
    assert quote.price == 33100
    assert quote.change == 700
    assert quote.volume == 12_000_000
    assert isinstance(quote.timestamp, datetime)


def test_parse_item_ceiling_floor() -> None:
    item = _vci_item("VNM", price=80000, change=0)
    quote = _parse_item(item)
    assert quote.ceiling > quote.ref_price
    assert quote.floor < quote.ref_price
    assert not quote.is_ceiling
    assert not quote.is_floor


def test_parse_item_at_ceiling() -> None:
    ref = 30000.0
    ceiling = round(ref * 1.07, -2)
    item = _vci_item("FPT", price=ceiling, change=ceiling - ref)
    item["listingInfo"]["ceiling"] = ceiling
    quote = _parse_item(item)
    assert quote.is_ceiling


def test_parse_item_missing_match_price_falls_back_to_ref() -> None:
    item = _vci_item("ACB")
    item["matchPrice"]["matchPrice"] = None
    quote = _parse_item(item)
    assert quote.price == item["listingInfo"]["refPrice"]


def test_parse_item_missing_time_uses_utcnow() -> None:
    item = _vci_item("MBB")
    item["matchPrice"]["time"] = None
    quote = _parse_item(item)
    assert isinstance(quote.timestamp, datetime)


async def test_fetch_quote_calls_post_and_returns_quote() -> None:
    raw_response = [_vci_item("HPG")]

    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            import json as _json
            return httpx.Response(200, json=raw_response)

    adapter = VCIAdapter()
    adapter._client = httpx.AsyncClient(
        base_url="https://trading.vietcap.com.vn/api/",
        transport=MockTransport(),
    )
    quote = await adapter.fetch_quote("HPG")
    assert quote.ticker == "HPG"
    assert quote.price == 33100


async def test_fetch_bulk_quotes_returns_multiple() -> None:
    items = [_vci_item("HPG"), _vci_item("VNM", price=80000, change=500)]

    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, json=items)

    adapter = VCIAdapter()
    adapter._client = httpx.AsyncClient(
        base_url="https://trading.vietcap.com.vn/api/",
        transport=MockTransport(),
    )
    quotes = await adapter.fetch_bulk_quotes(["HPG", "VNM"])
    assert len(quotes) == 2
    tickers = {q.ticker for q in quotes}
    assert tickers == {"HPG", "VNM"}
