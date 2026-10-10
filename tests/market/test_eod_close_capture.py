"""Wave C1 — QuoteService.capture_eod_close + Quote.close carry-over."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from src.market.adapters.mock import MockAdapter, _make_mock_quote
from src.market.quote_service import Quote, QuoteService, TradingHoursGuard


class _ClosedGuard:
    def is_market_open(self, now: datetime | None = None) -> bool:
        return False


class _ZeroVolumeAdapter(MockAdapter):
    """HPG không khớp lệnh cả phiên (volume=0) — không được gắn close."""

    async def fetch_bulk_quotes(self, tickers: list[str]) -> list[Quote]:
        out = []
        for q in await super().fetch_bulk_quotes(tickers):
            out.append(replace(q, volume=0) if q.ticker == "HPG" else q)
        return out


def test_settled_price_prefers_close() -> None:
    q = _make_mock_quote("VNM")
    assert q.close is None and q.settled_price == q.price
    assert replace(q, close=q.price + 100).settled_price == q.price + 100


@pytest.mark.asyncio
async def test_capture_sets_close_and_last_known_even_when_market_closed() -> None:
    svc = QuoteService(MockAdapter(), guard=_ClosedGuard())
    result = await svc.capture_eod_close(["vnm", "FPT", " "])
    assert result.error is None
    assert (result.requested, result.captured, result.no_match) == (2, 2, [])
    # Ngoài giờ get_quote trả last_known → phải mang close
    q = await svc.get_quote("VNM")
    assert q.close == q.price and q.settled_price == q.price
    bulk = await svc.get_bulk_quotes(["VNM", "FPT"])
    assert all(b.close is not None for b in bulk)


@pytest.mark.asyncio
async def test_capture_skips_no_match_ticker() -> None:
    svc = QuoteService(_ZeroVolumeAdapter(), guard=_ClosedGuard())
    result = await svc.capture_eod_close(["HPG", "VNM"])
    assert result.captured == 1 and result.no_match == ["HPG"]
    assert svc._cache.get_last_known("HPG") is None
    assert svc._cache.get_last_known("VNM").close is not None


@pytest.mark.asyncio
async def test_live_refetch_same_day_keeps_close() -> None:
    svc = QuoteService(MockAdapter(), guard=TradingHoursGuard(always=True))
    await svc.capture_eod_close(["VNM"])
    svc._cache._single.clear()  # ép fetch live lại (TTL hết)
    q = await svc.get_quote("VNM")
    assert q.close is not None, "fetch live cùng ngày không được xoá close đã chụp"


@pytest.mark.asyncio
async def test_refetch_next_day_drops_stale_close() -> None:
    svc = QuoteService(MockAdapter(), guard=TradingHoursGuard(always=True))
    old = replace(_make_mock_quote("VNM"), close=1.0, timestamp=datetime(2020, 1, 1, tzinfo=UTC))
    svc._cache._last_known["VNM"] = old
    q = await svc.get_quote("VNM")
    assert q.close is None


@pytest.mark.asyncio
async def test_capture_reports_adapter_error_without_raising() -> None:
    svc = QuoteService(MockAdapter(fail_tickers={"ERR"}), guard=_ClosedGuard())
    result = await svc.capture_eod_close(["ERR"])
    assert result.captured == 0 and result.error
