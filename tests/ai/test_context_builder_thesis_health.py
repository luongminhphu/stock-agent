"""Wave D4c: ContextBuilder truyền nguồn giá (TickerContext/quote) vào thesis health."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.ai.context_builder as mod
from src.ai.context_builder import ContextBuilder, _resolve_price_sources
from src.thesis.health_snapshot import ThesisHealthSnapshot


def _snap(ticker: str, flag: str) -> ThesisHealthSnapshot:
    return ThesisHealthSnapshot(
        thesis_id="1",
        ticker=ticker,
        title="",
        direction="BULLISH",
        health_score=0.5,
        days_since_review=1,
        distance_to_stop_pct=None,
        assumptions_total=0,
        assumptions_invalidated=0,
        last_verdict="UNREVIEWED",
        urgency_flag=flag,
    )


def test_resolve_price_sources_without_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.platform.bootstrap as boot

    monkeypatch.setattr(boot.container, "ticker_context_service", None)
    monkeypatch.setattr(boot.container, "quote_service", None)
    assert _resolve_price_sources() == (None, None)


def test_resolve_price_sources_with_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.platform.bootstrap as boot

    tcs, qs = object(), object()
    monkeypatch.setattr(boot.container, "ticker_context_service", tcs)
    monkeypatch.setattr(boot.container, "quote_service", qs)
    assert _resolve_price_sources() == (tcs, qs)


@pytest.mark.anyio
async def test_fetch_thesis_health_passes_price_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.thesis.health_snapshot as hs

    tcs, qs = object(), object()
    monkeypatch.setattr(mod, "_resolve_price_sources", lambda: (tcs, qs))
    builder = AsyncMock(return_value=[_snap("HPG", "AT_RISK"), _snap("VNM", "REVIEW_DUE")])
    monkeypatch.setattr(hs, "build_thesis_health_snapshots", builder)

    cb = ContextBuilder(SimpleNamespace())
    text = await cb._fetch_thesis_health("u1")

    builder.assert_awaited_once_with(
        cb._session, user_id="u1", ticker_context_service=tcs, quote_service=qs
    )
    assert text.startswith("Thesis health (2 active):")
    assert "AT_RISK/INVALIDATED: HPG" in text and "Cần review: VNM" in text
