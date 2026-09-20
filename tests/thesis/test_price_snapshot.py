"""Wave C3: helper load_price_snapshots — bulk context trước, get_quote sau."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.thesis.price_snapshot import PriceSnapshot, load_price_snapshots


def _ctx(price, atr14=None, quality="live"):
    return SimpleNamespace(price=price, atr14=atr14, source_quality=quality)


@pytest.mark.anyio
async def test_bulk_then_fallback_for_missing():
    tcs = SimpleNamespace(get_many=AsyncMock(return_value={"HPG": _ctx(25000.0, 400.0, "stale")}))
    qs = SimpleNamespace(get_quote=AsyncMock(return_value=SimpleNamespace(price=70000.0)))

    out = await load_price_snapshots(
        ["hpg", "VNM", "HPG"], ticker_context_service=tcs, quote_service=qs
    )

    tcs.get_many.assert_awaited_once_with(["HPG", "VNM"])
    qs.get_quote.assert_awaited_once_with("VNM")
    assert out["HPG"] == PriceSnapshot(price=25000.0, atr14=400.0, source_quality="stale")
    assert out["VNM"] == PriceSnapshot(price=70000.0)


@pytest.mark.anyio
async def test_context_failure_falls_back_entirely_and_quote_errors_are_skipped():
    tcs = SimpleNamespace(get_many=AsyncMock(side_effect=RuntimeError("down")))

    async def _q(sym):
        if sym == "BAD":
            raise RuntimeError("no quote")
        return SimpleNamespace(price=1.0)

    qs = SimpleNamespace(get_quote=AsyncMock(side_effect=_q))
    out = await load_price_snapshots(["BAD", "OK"], ticker_context_service=tcs, quote_service=qs)
    assert set(out) == {"OK"}


@pytest.mark.anyio
async def test_no_services_or_empty_returns_empty():
    assert await load_price_snapshots([], ticker_context_service=None, quote_service=None) == {}
    assert (
        await load_price_snapshots(["HPG"], ticker_context_service=None, quote_service=None) == {}
    )


def test_stop_distance_atr():
    snap = PriceSnapshot(price=23300.0, atr14=500.0)
    assert snap.stop_distance_atr(23000.0) == pytest.approx(0.6)
    assert snap.stop_distance_atr(None) is None
    assert PriceSnapshot(price=1.0, atr14=None).stop_distance_atr(0.5) is None
    assert PriceSnapshot(price=1.0, atr14=0.0).stop_distance_atr(0.5) is None
