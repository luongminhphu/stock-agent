"""Unit tests for PriceEnrichmentService."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.market.price_enrichment import PriceEnrichmentService
from src.market.quote_service import QuoteService
from src.market.adapters.mock import MockAdapter, _make_mock_quote
from src.readmodel.schemas import DashboardResponse, ThesisSummaryRow, WatchlistSnapshotRow


def _make_summary_row(ticker: str, entry_price: float | None = 50_000.0) -> ThesisSummaryRow:
    return ThesisSummaryRow(
        id=1,
        ticker=ticker,
        title=f"Test thesis {ticker}",
        status="active",
        score=70.0,
        entry_price=entry_price,
        target_price=65_000.0,
        stop_loss=45_000.0,
        upside_pct=30.0,
        risk_reward=3.0,
        current_price=None,
        pnl_pct=None,
        last_verdict="BULLISH",
        last_reviewed_at=None,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        assumption_count=2,
        invalid_assumption_count=0,
        catalyst_count=1,
        triggered_catalyst_count=0,
    )


def _make_dashboard(rows: list[ThesisSummaryRow]) -> DashboardResponse:
    return DashboardResponse(
        user_id="user_001",
        generated_at=datetime.now(timezone.utc),
        total_theses=len(rows),
        active_count=len(rows),
        invalidated_count=0,
        closed_count=0,
        avg_score=70.0,
        theses=rows,
    )


@pytest.mark.asyncio
async def test_enrich_dashboard_fills_current_price():
    qs = QuoteService(adapter=MockAdapter())
    svc = PriceEnrichmentService(qs)
    row = _make_summary_row("HPG")
    dashboard = _make_dashboard([row])

    result = await svc.enrich_dashboard(dashboard)

    assert result.theses[0].current_price is not None
    assert result.theses[0].current_price > 0


@pytest.mark.asyncio
async def test_enrich_dashboard_fills_pnl_pct():
    qs = QuoteService(adapter=MockAdapter())
    svc = PriceEnrichmentService(qs)
    row = _make_summary_row("HPG", entry_price=50_000.0)
    dashboard = _make_dashboard([row])

    result = await svc.enrich_dashboard(dashboard)
    enriched = result.theses[0]

    assert enriched.pnl_pct is not None
    expected = (enriched.current_price - 50_000.0) / 50_000.0 * 100
    assert abs(enriched.pnl_pct - expected) < 0.001


@pytest.mark.asyncio
async def test_enrich_dashboard_no_entry_price_skips_pnl():
    qs = QuoteService(adapter=MockAdapter())
    svc = PriceEnrichmentService(qs)
    row = _make_summary_row("HPG", entry_price=None)
    dashboard = _make_dashboard([row])

    result = await svc.enrich_dashboard(dashboard)
    assert result.theses[0].pnl_pct is None


@pytest.mark.asyncio
async def test_enrich_dashboard_empty_rows():
    qs = QuoteService(adapter=MockAdapter())
    svc = PriceEnrichmentService(qs)
    dashboard = _make_dashboard([])
    result = await svc.enrich_dashboard(dashboard)
    assert result.theses == []


@pytest.mark.asyncio
async def test_enrich_dashboard_fallback_on_bulk_fail():
    """If bulk fetch fails, individual fetches are used as fallback."""
    qs = QuoteService(adapter=MockAdapter())
    # Patch bulk to fail, single to succeed
    qs.get_bulk_quotes = AsyncMock(side_effect=RuntimeError("bulk fail"))
    qs.get_quote = AsyncMock(return_value=_make_mock_quote("HPG"))
    svc = PriceEnrichmentService(qs)
    row = _make_summary_row("HPG")
    dashboard = _make_dashboard([row])

    result = await svc.enrich_dashboard(dashboard)
    assert result.theses[0].current_price is not None


@pytest.mark.asyncio
async def test_enrich_watchlist_fills_price():
    qs = QuoteService(adapter=MockAdapter())
    svc = PriceEnrichmentService(qs)
    rows = [
        WatchlistSnapshotRow(
            ticker="VNM",
            note=None,
            thesis_id=None,
            thesis_title=None,
            thesis_status=None,
            current_price=None,
            added_at=datetime.now(timezone.utc),
        )
    ]
    result = await svc.enrich_watchlist(rows)
    assert result[0].current_price is not None


@pytest.mark.asyncio
async def test_get_prices_returns_map():
    qs = QuoteService(adapter=MockAdapter())
    svc = PriceEnrichmentService(qs)
    prices = await svc.get_prices(["HPG", "VNM"])
    assert "HPG" in prices
    assert "VNM" in prices
    assert all(v > 0 for v in prices.values())
