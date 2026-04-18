"""Read-model API routes.

Owner: api segment — thin adapter only.
Delegates 100% to readmodel services + price enrichment from market segment.
No business logic here.

Endpoints:
    GET /readmodel/dashboard/{user_id}                   — legacy full dashboard (DashboardResponse)
    GET /readmodel/dashboard/{user_id}/watchlist         — legacy watchlist snapshot
    GET /readmodel/dashboard/{user_id}/stats             — KPI tong quan
    GET /readmodel/dashboard/{user_id}/theses            — list thesis + filter
    GET /readmodel/dashboard/{user_id}/theses/{id}       — thesis detail
    GET /readmodel/dashboard/{user_id}/catalysts/upcoming — upcoming catalysts
    GET /readmodel/dashboard/{user_id}/scan/latest       — latest scan snapshot
    GET /readmodel/dashboard/{user_id}/brief/latest      — latest brief snapshot
    GET /readmodel/dashboard/{user_id}/backtesting/verdict-accuracy    — verdict accuracy
    GET /readmodel/dashboard/{user_id}/backtesting/thesis-performances — PnL per thesis
    GET /readmodel/dashboard/{user_id}/backtesting/price-snapshots/{id} — chart data
    GET /readmodel/leaderboard/{user_id}                 — ranked leaderboard
    GET /readmodel/thesis/{thesis_id}/timeline           — event timeline
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.market.price_enrichment import PriceEnrichmentService
from src.platform.bootstrap import get_quote_service
from src.platform.db import get_session
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
# Legacy endpoints — backward compat
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}", response_model=DashboardResponse)
async def get_dashboard(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    enrichment: Annotated[PriceEnrichmentService, Depends(_enrichment)],
) -> DashboardResponse:
    """Full dashboard payload with live prices injected (legacy)."""
    svc = DashboardService(session)
    response = await svc.get_dashboard(user_id)
    return await enrichment.enrich_dashboard(response)  # type: ignore[return-value]


@router.get("/dashboard/{user_id}/watchlist", response_model=list[WatchlistSnapshotRow])
async def get_watchlist_snapshot(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    enrichment: Annotated[PriceEnrichmentService, Depends(_enrichment)],
) -> list[WatchlistSnapshotRow]:
    """Watchlist items enriched with thesis summary + live prices (legacy)."""
    svc = DashboardService(session)
    rows = await svc.get_watchlist_snapshot(user_id)
    return await enrichment.enrich_watchlist(rows)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. Stats — KPI tong quan
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/stats")
async def get_stats(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """KPI tong quan: open theses, verdict distribution, risky count, upcoming catalysts."""
    svc = DashboardService(session)
    return await svc.get_stats(user_id)


# ---------------------------------------------------------------------------
# 2. Theses list
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/theses")
async def get_theses_list(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    status: Annotated[
        str, Query(description="active | invalidated | closed | paused | all")
    ] = "active",
    ticker: Annotated[str | None, Query(description="Filter theo ticker, e.g. VNM")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[dict[str, Any]]:
    """List thesis voi last review, assumption/catalyst counts. Filter theo status va ticker."""
    svc = DashboardService(session)
    return await svc.get_theses_list(user_id, status=status, ticker=ticker, limit=limit)


# ---------------------------------------------------------------------------
# 3. Thesis detail
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/theses/{thesis_id}")
async def get_thesis_detail(
    user_id: str,
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Full thesis detail: thesis + reviews + assumptions + catalysts."""
    svc = DashboardService(session)
    result = await svc.get_thesis_detail(user_id, thesis_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Thesis {thesis_id} not found")
    return result


# ---------------------------------------------------------------------------
# 4. Upcoming catalysts
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/catalysts/upcoming")
async def get_upcoming_catalysts(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    days: Annotated[int, Query(ge=1, le=90, description="So ngay tiep theo can xem")] = 30,
) -> list[dict[str, Any]]:
    """Catalysts dang PENDING trong vong <days> ngay toi, chi thesis ACTIVE."""
    svc = DashboardService(session)
    return await svc.get_upcoming_catalysts(user_id, days=days)


# ---------------------------------------------------------------------------
# 5. Latest scan snapshot
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/scan/latest")
async def get_scan_latest(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """WatchlistScan snapshot gan nhat cua user."""
    svc = DashboardService(session)
    result = await svc.get_scan_latest(user_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No scan snapshot found")
    return result


# ---------------------------------------------------------------------------
# 6. Latest brief snapshot
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/brief/latest")
async def get_brief_latest(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    phase: Annotated[
        Literal["morning", "eod"],
        Query(description="morning | eod"),
    ] = "morning",
) -> dict[str, Any]:
    """BriefSnapshot gan nhat theo phase (morning / eod)."""
    svc = DashboardService(session)
    result = await svc.get_brief_latest(user_id, phase=phase)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No {phase} brief snapshot found")
    return result


# ---------------------------------------------------------------------------
# 7. Backtesting — verdict accuracy
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/backtesting/verdict-accuracy")
async def get_verdict_accuracy(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, Any]]:
    """Accuracy theo verdict: BULLISH/BEARISH/NEUTRAL/WATCHLIST vs pnl_pct thuc te."""
    svc = DashboardService(session)
    return await svc.get_verdict_accuracy(user_id)


# ---------------------------------------------------------------------------
# 8. Backtesting — thesis performances
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/backtesting/thesis-performances")
async def get_thesis_performances(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ticker: Annotated[str | None, Query(description="Filter theo ticker")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    """Aggregate PnL per thesis tu ThesisSnapshot: avg/max/min pnl_pct."""
    svc = DashboardService(session)
    return await svc.get_thesis_performances(user_id, ticker=ticker, limit=limit)


# ---------------------------------------------------------------------------
# 9. Backtesting — price snapshots (chart data)
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/backtesting/price-snapshots/{thesis_id}")
async def get_price_snapshots(
    user_id: str,
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Chuoi gia theo thoi gian cua mot thesis, kem verdict tai moi snapshot."""
    svc = DashboardService(session)
    result = await svc.get_price_snapshots(user_id, thesis_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Thesis {thesis_id} not found")
    return result


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


@router.get("/leaderboard/{user_id}", response_model=LeaderboardResponse)
async def get_leaderboard(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    sort_by: Annotated[Literal["score", "pnl"], Query()] = "score",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
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
