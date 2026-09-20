"""Wave F3 — build_today_loop_digest thuộc readmodel; api/routes/today_loop chỉ là adapter."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.readmodel import today_loop_digest as mod


def _svc(**overrides: Any) -> Any:
    svc = AsyncMock()
    svc.get_theses_list = AsyncMock(
        return_value=[
            {
                "id": 1,
                "ticker": "HPG",
                "score": 55.0,
                "health_rank": "ok",
                "days_since_review": 3,
                "last_verdict": "BULLISH",
                "pnl_pct": 2.0,
            },
            {
                "id": 2,
                "ticker": "VNM",
                "score": 80.0,
                "health_rank": "stale",
                "days_since_review": 30,
                "last_verdict": None,
                "pnl_pct": None,
            },
            {
                "id": 3,
                "ticker": "FPT",
                "score": 85.0,
                "health_rank": "ok",
                "days_since_review": 2,
                "last_verdict": "BULLISH",
                "pnl_pct": 1.0,
            },
        ]
    )
    svc.get_attention_needed = AsyncMock(
        return_value=SimpleNamespace(items=[SimpleNamespace(model_dump=lambda: {"ticker": "HPG"})])
    )
    svc.get_recent_signals = AsyncMock(return_value=[{"ticker": "HPG", "strength": 0.9}])
    svc.get_scan_latest = AsyncMock(return_value={"market_bias": "BULLISH", "green_pct": 61})
    svc.get_brief_latest = AsyncMock(
        return_value={"id": 9, "summary": "Sáng nay...", "phase": "morning"}
    )
    for k, v in overrides.items():
        setattr(svc, k, v)
    return svc


@pytest.mark.asyncio
async def test_digest_aggregates_and_flags_theses() -> None:
    quote_service = AsyncMock()
    quote_service.get_bulk_quotes = AsyncMock(
        return_value=[SimpleNamespace(ticker="HPG", price=25000.0)]
    )
    with patch.object(mod, "DashboardService", return_value=_svc()):
        out = await mod.build_today_loop_digest(
            session=AsyncMock(), user_id="u1", quote_service=quote_service
        )

    assert out["stale_sources"] == []
    assert out["meta"]["attention_count"] == 1
    assert out["meta"]["signal_count"] == 1
    assert out["market_mood"]["bias"] == "BULLISH"
    assert out["brief_summary"]["brief_id"] == 9
    flags = {d["ticker"]: d["flags"] for d in out["thesis_digest"]}
    assert flags == {"HPG": ["low_conviction"], "VNM": ["overdue_review"]}
    assert out["meta"]["thesis_needing_action"] == 2
    quote_service.get_bulk_quotes.assert_awaited_once()


@pytest.mark.asyncio
async def test_digest_partial_failure_reports_stale_sources() -> None:
    svc = _svc(
        get_recent_signals=AsyncMock(side_effect=RuntimeError("db down")),
        get_brief_latest=AsyncMock(side_effect=RuntimeError("db down")),
    )
    with patch.object(mod, "DashboardService", return_value=svc):
        out = await mod.build_today_loop_digest(
            session=AsyncMock(), user_id="u1", enrich_prices=False
        )

    assert set(out["stale_sources"]) == {"top_signals", "brief"}
    assert out["top_signals"] == []
    assert out["brief_summary"] == {}
    assert out["meta"]["has_brief"] is False
    assert out["meta"]["attention_count"] == 1  # nguồn còn lại vẫn có dữ liệu


def test_api_route_is_thin_adapter() -> None:
    import inspect

    from src.api.routes import today_loop

    assert not hasattr(today_loop, "_build_today_loop")
    assert "DashboardService(" not in inspect.getsource(today_loop)
