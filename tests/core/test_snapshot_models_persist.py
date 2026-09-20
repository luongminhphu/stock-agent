"""Wave E1b: intelligence_snapshots / global_risk_snapshots thuộc core; store readmodel persist qua upsert_rows."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.models import GlobalRiskSnapshot, IntelligenceSnapshot
from src.platform.db import AsyncSessionLocal
from src.readmodel.global_risk_store import GlobalRiskStore, _persist_risk_snapshot
from src.readmodel.intelligence_snapshot import (
    _persist_intelligence_snapshot,
    load_intelligence_snapshots_from_db,
)


@pytest.mark.anyio
async def test_global_risk_persist_and_warm_load() -> None:
    verdict = SimpleNamespace(model_dump=lambda: {"risk": "HIGH"})
    await _persist_risk_snapshot(AsyncSessionLocal, "u1", {"HPG", "VNM"}, verdict)
    await _persist_risk_snapshot(AsyncSessionLocal, "u1", {"SSI"}, verdict)  # upsert

    store = GlobalRiskStore(session_factory=AsyncSessionLocal)
    assert await store.warm_load() == 1
    assert store.get_flagged_tickers("u1") == {"SSI"}


@pytest.mark.anyio
async def test_intelligence_snapshot_persist_and_load() -> None:
    report = SimpleNamespace(model_dump=lambda: {"headline": "x"})
    await _persist_intelligence_snapshot(AsyncSessionLocal, "u1", report, "scheduler")
    await _persist_intelligence_snapshot(AsyncSessionLocal, "u1", report, "manual")

    rows = await load_intelligence_snapshots_from_db(AsyncSessionLocal)
    assert list(rows) == ["u1"] and rows["u1"]["trigger_source"] == "manual"


def test_core_owns_snapshot_models() -> None:
    assert GlobalRiskSnapshot.__module__ == "src.core.models"
    assert IntelligenceSnapshot.__module__ == "src.core.models"
