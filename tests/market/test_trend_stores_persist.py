"""Wave E1a: TrendSnapshotStore / TrendPredictionStore thuộc market, persist qua upsert_rows."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.market.trend_prediction_store import TrendPredictionStore, _persist_prediction
from src.market.trend_snapshot_store import TrendSnapshotStore, _persist_snapshot
from src.platform.db import AsyncSessionLocal


@pytest.mark.anyio
async def test_trend_snapshot_persist_and_warm_load() -> None:
    await _persist_snapshot(AsyncSessionLocal, "hpg", {"composite": 0.4})
    await _persist_snapshot(AsyncSessionLocal, "HPG", {"composite": 0.7})  # upsert

    store = TrendSnapshotStore(session_factory=AsyncSessionLocal)
    assert await store.warm_load() == 1
    assert store.get_last("HPG")["bundle"] == {"composite": 0.7}


@pytest.mark.anyio
async def test_trend_prediction_persist_and_warm_load() -> None:
    pred = SimpleNamespace(
        verdict="BUY", confidence=0.8, model_dump=lambda: {"verdict": "BUY", "confidence": 0.8}
    )
    await _persist_prediction(AsyncSessionLocal, "vnm", pred)

    store = TrendPredictionStore(session_factory=AsyncSessionLocal)
    assert await store.warm_load() == 1
    assert store.get_verdict("VNM") == "BUY"
    assert store.get_confidence("VNM") == pytest.approx(0.8)


def test_readmodel_shims_reexport_market_classes() -> None:
    from src.market import models as mm
    from src.readmodel import trend_prediction_store as rp
    from src.readmodel import trend_snapshot_store as rs
    from src.readmodel.models import MarketQuoteCache, TrendPrediction, TrendSnapshot

    assert rs.TrendSnapshotStore is TrendSnapshotStore
    assert rp.TrendPredictionStore is TrendPredictionStore
    assert (TrendSnapshot, TrendPrediction, MarketQuoteCache) == (
        mm.TrendSnapshot,
        mm.TrendPrediction,
        mm.MarketQuoteCache,
    )
