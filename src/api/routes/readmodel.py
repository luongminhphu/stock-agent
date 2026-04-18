"""Read-model API routes.

Owner: api segment — thin adapter only.
Delegates 100% to readmodel services + price enrichment from market segment.
No business logic here.
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.bootstrap import get_quote_service
from src.platform.db import get_session
from src.market.price_enrichment import PriceEnrichmentService
from src.readmodel.dashboard_service import DashboardService
from src.readmodel.leaderboard_service import LeaderboardService
from src.readmodel.schemas import (
    DashboardResponse,
    LeaderboardResponse,
    ThesisTimelineResponse,
    WatchlistSnapshotRow,
)
from src.readmodel.timeline_service import ThesisTimelineService

router = APIRouter(prefix="/readmodel", tags=["readmodel"])


def _enrichment() -> PriceEnrichmentService:
    """Dependency: PriceEnrichmentService wired to the bootstrapped QuoteService."""
    from src.market.quote_service import QuoteService
    qs: QuoteService = get_quote_service()  # type: ignore[assignment]
    return PriceEnrichmentService(qs)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}", response_model=DashboardResponse)
async def get_dashboard(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    enrichment: Annotated[PriceEnrichmentService, Depends(_enrichment)],
) -> DashboardResponse:
    """Full dashboard payload with live prices injected."""
    svc = DashboardService(session)
    response = await svc.get_dashboard(user_id)
    return await enrichment.enrich_dashboard(response)  # type: ignore[return-value]


@router.get("/dashboard/{user_id}/watchlist", response_model=list[WatchlistSnapshotRow])
async def get_watchlist_snapshot(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    enrichment: Annotated[PriceEnrichmentService, Depends(_enrichment)],
) -> list[WatchlistSnapshotRow]:
    """Watchlist items enriched with thesis summary + live prices."""
    svc = DashboardService(session)
    rows = await svc.get_watchlist_snapshot(user_id)
    return await enrichment.enrich_watchlist(rows)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


@router.get("/leaderboard/{user_id}", response_model=LeaderboardResponse)
async def get_leaderboard(
    user_id: str,
    sort_by: Annotated[Literal["score", "pnl"], Query()] = "score",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LeaderboardResponse:
    """Ranked thesis leaderboard. sort_by=score|pnl."""
    svc = LeaderboardService(session)
    return await svc.get_leaderboard(user_id, sort_by=sort_by, limit=limit)


# ---------------------------------------------------------------------------
# Thesis timeline
# ---------------------------------------------------------------------------


@router.get("/thesis/{thesis_id}/timeline", response_model=ThesisTimelineResponse)
async def get_thesis_timeline(
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ThesisTimelineResponse:
    """Chronological event log for a single thesis."""
    svc = ThesisTimelineService(session)
    result = await svc.get_timeline(thesis_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Thesis {thesis_id} not found")
    return result
