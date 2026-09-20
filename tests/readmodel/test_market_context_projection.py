"""Wave U2a — readmodel chiếu market.TickerContext vào row thesis.

Kiểm tra pure function `_market_context_fields` (không cần DB) và helper
`price_map_from_context` / `build_market_context_map` trong enrichment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from src.market.quote_service import Quote
from src.market.ticker_context import TickerContext
from src.readmodel.enrichment import build_market_context_map, price_map_from_context
from src.readmodel.thesis_query_service import _market_context_fields

_NOW = datetime(2026, 9, 18, 3, 0, tzinfo=UTC)


def _quote(ticker: str = "VNM", price: float = 70_000.0) -> Quote:
    return Quote(
        ticker=ticker,
        price=price,
        change=500.0,
        change_pct=0.72,
        volume=1_200_000,
        value=price * 1_200_000,
        open=69_500.0,
        high=70_500.0,
        low=69_000.0,
        ref_price=69_500.0,
        ceiling=74_350.0,
        floor=64_650.0,
        timestamp=_NOW,
    )


def _ctx(price: float = 70_000.0, atr14: float | None = 1_500.0) -> TickerContext:
    return TickerContext(
        ticker="VNM",
        as_of=_NOW,
        quote=_quote(price=price),
        ma20=68_000.0,
        ma50=66_000.0,
        rsi14=61.234,
        atr14=atr14,
        vol_ratio_20=1.456,
        hi_52w=80_000.0,
        lo_52w=55_000.0,
        bars=120,
        trend_state="UPTREND",
        source_quality="live",
    )


# ---------------------------------------------------------------------------
# _market_context_fields
# ---------------------------------------------------------------------------


def test_fields_all_none_when_ctx_missing() -> None:
    out = _market_context_fields(
        None, current_price=70_000.0, stop_loss=65_000.0, direction_upper="BULLISH"
    )
    assert out["change_pct"] is None
    assert out["source_quality"] is None
    assert out["near_stop"] is None
    assert out["stop_distance_atr"] is None


def test_fields_projected_from_ctx() -> None:
    out = _market_context_fields(
        _ctx(), current_price=70_000.0, stop_loss=65_000.0, direction_upper="BULLISH"
    )
    assert out["change_pct"] == 0.72
    assert out["volume"] == 1_200_000
    assert out["is_ceiling"] is False and out["is_floor"] is False
    assert out["source_quality"] == "live"
    assert out["trend_state"] == "UPTREND"
    assert out["rsi14"] == 61.2
    assert out["vol_ratio_20"] == 1.46
    assert out["dist_to_ma20_pct"] == pytest.approx(2.94, abs=0.01)
    assert out["market_as_of"] == _NOW.isoformat()
    # (70000 - 65000) / 1500 = 3.33 ATR → chưa gần stop
    assert out["stop_distance_atr"] == 3.33
    assert out["near_stop"] is False


def test_near_stop_true_when_within_one_atr_bullish() -> None:
    out = _market_context_fields(
        _ctx(price=66_000.0),
        current_price=66_000.0,
        stop_loss=65_000.0,
        direction_upper="BULLISH",
    )
    assert out["stop_distance_atr"] == 0.67
    assert out["near_stop"] is True


def test_near_stop_bearish_inverts_sign() -> None:
    # BEARISH: stop ở trên giá; price 66_000 < stop 67_000 → còn cách 0.67 ATR
    out = _market_context_fields(
        _ctx(price=66_000.0),
        current_price=66_000.0,
        stop_loss=67_000.0,
        direction_upper="BEARISH",
    )
    assert out["stop_distance_atr"] == 0.67
    assert out["near_stop"] is True


def test_breached_is_not_near_stop() -> None:
    out = _market_context_fields(
        _ctx(price=64_000.0),
        current_price=64_000.0,
        stop_loss=65_000.0,
        direction_upper="BULLISH",
    )
    assert out["stop_distance_atr"] == -0.67
    assert out["near_stop"] is False


def test_no_atr_means_no_stop_distance() -> None:
    out = _market_context_fields(
        _ctx(atr14=None), current_price=70_000.0, stop_loss=65_000.0, direction_upper="BULLISH"
    )
    assert out["stop_distance_atr"] is None
    assert out["near_stop"] is None
    # indicator khác vẫn có
    assert out["trend_state"] == "UPTREND"


# ---------------------------------------------------------------------------
# enrichment helpers
# ---------------------------------------------------------------------------


def test_price_map_from_context() -> None:
    assert price_map_from_context({"VNM": _ctx(price=70_000.0)}) == {"VNM": 70_000.0}
    assert price_map_from_context({}) == {}


async def test_build_market_context_map_uses_get_many() -> None:
    svc = AsyncMock()
    svc.get_many.return_value = {"VNM": _ctx()}
    with patch("src.readmodel.enrichment.get_ticker_context_service", return_value=svc):
        out = await build_market_context_map(["VNM"])
    assert set(out) == {"VNM"}
    svc.get_many.assert_awaited_once_with(["VNM"])


async def test_build_market_context_map_empty_on_error() -> None:
    svc = AsyncMock()
    svc.get_many.side_effect = RuntimeError("boom")
    with patch("src.readmodel.enrichment.get_ticker_context_service", return_value=svc):
        assert await build_market_context_map(["VNM"]) == {}


async def test_build_market_context_map_skips_empty_tickers() -> None:
    with patch("src.readmodel.enrichment.get_ticker_context_service") as gt:
        assert await build_market_context_map([]) == {}
        gt.assert_not_called()
