"""Integration tests for /api/v1/readmodel/* routes.

Readmodel services (DashboardService, LeaderboardService, ThesisTimelineService)
are patched with AsyncMock to avoid DB setup complexity.
Price enrichment is also patched to return input unchanged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.readmodel.schemas import (
    DashboardResponse,
    LeaderboardResponse,
    ThesisTimelineResponse,
)


def _empty_dashboard(user_id: str = "user-test-001") -> DashboardResponse:
    return DashboardResponse(
        user_id=user_id,
        open_thesis_count=0,
        closed_thesis_count=0,
        avg_score=None,
        watchlist_count=0,
        watchlist_snapshot=[],
        top_theses=[],
    )


def _empty_leaderboard(user_id: str = "user-test-001") -> LeaderboardResponse:
    return LeaderboardResponse(user_id=user_id, sort_by="score", rows=[], total=0)


def _empty_timeline(thesis_id: int = 1) -> ThesisTimelineResponse:
    return ThesisTimelineResponse(thesis_id=thesis_id, ticker="HPG", events=[])


# ---------------------------------------------------------------------------
# GET /readmodel/dashboard/{user_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_returns_200(bootstrapped_client):
    dashboard = _empty_dashboard()
    with (
        patch(
            "src.readmodel.dashboard_service.DashboardService.get_dashboard",
            new_callable=AsyncMock,
            return_value=dashboard,
        ),
        patch(
            "src.market.price_enrichment.PriceEnrichmentService.enrich_dashboard",
            new_callable=AsyncMock,
            return_value=dashboard,
        ),
    ):
        r = await bootstrapped_client.get("/api/v1/readmodel/dashboard/user-test-001")

    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "user-test-001"
    assert body["open_thesis_count"] == 0


@pytest.mark.asyncio
async def test_dashboard_watchlist_snapshot(bootstrapped_client):
    snapshot = []
    with (
        patch(
            "src.readmodel.dashboard_service.DashboardService.get_watchlist_snapshot",
            new_callable=AsyncMock,
            return_value=snapshot,
        ),
        patch(
            "src.market.price_enrichment.PriceEnrichmentService.enrich_watchlist",
            new_callable=AsyncMock,
            return_value=snapshot,
        ),
    ):
        r = await bootstrapped_client.get("/api/v1/readmodel/dashboard/user-test-001/watchlist")

    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# GET /readmodel/leaderboard/{user_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leaderboard_returns_200(bootstrapped_client):
    leaderboard = _empty_leaderboard()
    with patch(
        "src.readmodel.leaderboard_service.LeaderboardService.get_leaderboard",
        new_callable=AsyncMock,
        return_value=leaderboard,
    ):
        r = await bootstrapped_client.get("/api/v1/readmodel/leaderboard/user-test-001")

    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "user-test-001"
    assert body["rows"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_leaderboard_sort_by_pnl(bootstrapped_client):
    leaderboard = _empty_leaderboard()
    leaderboard.sort_by = "pnl"
    with patch(
        "src.readmodel.leaderboard_service.LeaderboardService.get_leaderboard",
        new_callable=AsyncMock,
        return_value=leaderboard,
    ):
        r = await bootstrapped_client.get("/api/v1/readmodel/leaderboard/user-test-001?sort_by=pnl")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_leaderboard_invalid_sort_by_422(bootstrapped_client):
    """Invalid sort_by value rejected at FastAPI validation layer."""
    r = await bootstrapped_client.get("/api/v1/readmodel/leaderboard/user-test-001?sort_by=invalid")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /readmodel/thesis/{thesis_id}/timeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeline_returns_200(bootstrapped_client):
    timeline = _empty_timeline(thesis_id=1)
    with patch(
        "src.readmodel.timeline_service.ThesisTimelineService.get_timeline",
        new_callable=AsyncMock,
        return_value=timeline,
    ):
        r = await bootstrapped_client.get("/api/v1/readmodel/thesis/1/timeline")

    assert r.status_code == 200
    body = r.json()
    assert body["thesis_id"] == 1
    assert body["ticker"] == "HPG"
    assert body["events"] == []


@pytest.mark.asyncio
async def test_timeline_not_found_404(bootstrapped_client):
    with patch(
        "src.readmodel.timeline_service.ThesisTimelineService.get_timeline",
        new_callable=AsyncMock,
        return_value=None,
    ):
        r = await bootstrapped_client.get("/api/v1/readmodel/thesis/999/timeline")
    assert r.status_code == 404
