"""Unit + integration tests for readmodel.DashboardService.

Uses in-memory SQLite via session fixture (tests/conftest.py).
No HTTP calls, no AI calls.
Verifies read queries against real ORM models.
"""
from __future__ import annotations

import pytest

from src.readmodel.dashboard_service import DashboardService
from src.thesis.service import CreateThesisInput, ThesisService
from src.watchlist.service import AddToWatchlistInput, WatchlistService

USER = "dash_user"


async def test_dashboard_empty_user(session):
    svc = DashboardService(session)
    resp = await svc.get_dashboard(user_id=USER)
    assert resp.user_id == USER
    assert resp.total_theses == 0
    assert resp.active_count == 0
    assert resp.theses == []
    assert resp.avg_score is None


async def test_dashboard_counts_one_active(session):
    thesis_svc = ThesisService(session)
    await thesis_svc.create(
        CreateThesisInput(user_id=USER, ticker="HPG", title="Steel play")
    )
    await session.flush()

    svc = DashboardService(session)
    resp = await svc.get_dashboard(user_id=USER)
    assert resp.total_theses == 1
    assert resp.active_count == 1
    assert resp.invalidated_count == 0
    assert resp.closed_count == 0


async def test_dashboard_counts_mixed_statuses(session):
    thesis_svc = ThesisService(session)
    t1 = await thesis_svc.create(CreateThesisInput(user_id=USER, ticker="VCB", title="Bank"))
    t2 = await thesis_svc.create(CreateThesisInput(user_id=USER, ticker="FPT", title="Tech"))
    t3 = await thesis_svc.create(CreateThesisInput(user_id=USER, ticker="VNM", title="Dairy"))
    await session.flush()
    await thesis_svc.close(thesis_id=t2.id, user_id=USER)
    await thesis_svc.invalidate(thesis_id=t3.id, user_id=USER)
    await session.flush()

    svc = DashboardService(session)
    resp = await svc.get_dashboard(user_id=USER)
    assert resp.total_theses == 3
    assert resp.active_count == 1
    assert resp.closed_count == 1
    assert resp.invalidated_count == 1


async def test_dashboard_upside_and_rr_computed(session):
    thesis_svc = ThesisService(session)
    await thesis_svc.create(
        CreateThesisInput(
            user_id=USER,
            ticker="HPG",
            title="Upside test",
            entry_price=20_000,
            target_price=30_000,
            stop_loss=16_000,
        )
    )
    await session.flush()

    svc = DashboardService(session)
    resp = await svc.get_dashboard(user_id=USER)
    row = resp.theses[0]
    assert row.upside_pct == pytest.approx(50.0)
    # R/R = (30k-20k) / (20k-16k) = 10k/4k = 2.5
    assert row.risk_reward == pytest.approx(2.5)


async def test_dashboard_no_cross_user_leak(session):
    thesis_svc = ThesisService(session)
    await thesis_svc.create(CreateThesisInput(user_id="other_user", ticker="TCB", title="Other"))
    await session.flush()

    svc = DashboardService(session)
    resp = await svc.get_dashboard(user_id=USER)
    assert resp.total_theses == 0


async def test_dashboard_last_verdict_populated(session):
    from src.ai.agents.thesis_review import ThesisReviewAgent
    from src.thesis.review_service import ReviewService
    from tests.ai.conftest import MockPerplexityClient

    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(
        CreateThesisInput(user_id=USER, ticker="MWG", title="Retail")
    )
    await session.flush()

    mock = MockPerplexityClient({
        "verdict": "BULLISH",
        "confidence": 0.9,
        "risk_signals": [],
        "next_watch_items": [],
        "reasoning": "Strong thesis.",
        "assumption_updates": [],
        "catalyst_status": [],
    })
    review_svc = ReviewService(session=session, agent=ThesisReviewAgent(mock))
    await review_svc.review_thesis(thesis_id=thesis.id, user_id=USER)
    await session.flush()

    svc = DashboardService(session)
    resp = await svc.get_dashboard(user_id=USER)
    row = resp.theses[0]
    assert row.last_verdict is not None
    assert "BULLISH" in row.last_verdict


async def test_watchlist_snapshot_empty(session):
    svc = DashboardService(session)
    rows = await svc.get_watchlist_snapshot(user_id=USER)
    assert rows == []


async def test_watchlist_snapshot_returns_items(session):
    wl_svc = WatchlistService(session)
    await wl_svc.add(AddToWatchlistInput(user_id=USER, ticker="HPG"))
    await wl_svc.add(AddToWatchlistInput(user_id=USER, ticker="VCB"))
    await session.flush()

    svc = DashboardService(session)
    rows = await svc.get_watchlist_snapshot(user_id=USER)
    tickers = [r.ticker for r in rows]
    assert "HPG" in tickers
    assert "VCB" in tickers


async def test_watchlist_snapshot_joins_thesis(session):
    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(
        CreateThesisInput(user_id=USER, ticker="FPT", title="Tech long")
    )
    await session.flush()

    wl_svc = WatchlistService(session)
    await wl_svc.add(AddToWatchlistInput(user_id=USER, ticker="FPT", thesis_id=thesis.id))
    await session.flush()

    svc = DashboardService(session)
    rows = await svc.get_watchlist_snapshot(user_id=USER)
    fpt = next(r for r in rows if r.ticker == "FPT")
    assert fpt.thesis_title == "Tech long"
    assert fpt.thesis_status == "active"


async def test_watchlist_snapshot_no_cross_user_leak(session):
    wl_svc = WatchlistService(session)
    await wl_svc.add(AddToWatchlistInput(user_id="other", ticker="NVL"))
    await session.flush()

    svc = DashboardService(session)
    rows = await svc.get_watchlist_snapshot(user_id=USER)
    assert all(r.ticker != "NVL" for r in rows)
