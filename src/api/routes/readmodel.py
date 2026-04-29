"""Read-model API routes.

Owner: api segment — thin adapter only.
Delegates 100% to readmodel services + price enrichment from market segment.
No heavy business logic here.

Single-user mode:
- If owner_user_id is configured, the alias endpoints without /{user_id}
  will automatically use that user id.
- Multi-user endpoints remain intact for backward compatibility.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.bootstrap import get_quote_service
from src.platform.config import settings
from src.api.deps import get_db
from src.readmodel.dashboard_service import DashboardService
from src.readmodel.leaderboard_service import LeaderboardService
from src.readmodel.schemas import (
    LeaderboardResponse,
    ThesisTimelineResponse,
)
from src.readmodel.timeline_service import ThesisTimelineService
from src.watchlist.scan_service import ScanService

router = APIRouter(prefix="/readmodel", tags=["readmodel"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _paginated(items: list) -> dict[str, Any]:
    """Wrap list thanh shape nhat quan: {items, total}."""
    return {"items": items, "total": len(items)}


def _default_user_id() -> str:
    if not settings.owner_user_id:
        raise HTTPException(
            status_code=500,
            detail="owner_user_id is not configured. Set it in .env for single-user mode.",
        )
    return settings.owner_user_id


async def _ensure_scan_snapshot(
    session: AsyncSession,
    user_id: str,
) -> dict[str, Any] | None:
    svc = DashboardService(session)
    latest = await svc.get_scan_latest(user_id)
    if latest is not None:
        return latest

    scan_svc = ScanService(
        session=session,
        quote_service=get_quote_service(),
    )
    await scan_svc.scan_user_if_stale(user_id=user_id, max_age_minutes=30)
    return await svc.get_scan_latest(user_id)


# ---------------------------------------------------------------------------
# 1. Stats — KPI tong quan
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/stats")
async def get_stats(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session)
    return await svc.get_stats(user_id)


@router.get("/dashboard/stats")
async def get_stats_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session)
    return await svc.get_stats(_default_user_id())


# ---------------------------------------------------------------------------
# 2. Theses list
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/theses")
async def get_theses_list(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    status: Annotated[str, Query()] = "active",
    ticker: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict[str, Any]:
    svc = DashboardService(session)
    items = await svc.get_theses_list(user_id, status=status, ticker=ticker, limit=limit)
    return _paginated(items)


@router.get("/dashboard/theses")
async def get_theses_list_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    status: Annotated[str, Query()] = "active",
    ticker: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict[str, Any]:
    svc = DashboardService(session)
    items = await svc.get_theses_list(
        _default_user_id(),
        status=status,
        ticker=ticker,
        limit=limit,
    )
    return _paginated(items)


# ---------------------------------------------------------------------------
# 3. Thesis detail
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/theses/{thesis_id}")
async def get_thesis_detail(
    user_id: str,
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session)
    result = await svc.get_thesis_detail(user_id, thesis_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Thesis {thesis_id} not found")
    return result


@router.get("/dashboard/theses/{thesis_id}")
async def get_thesis_detail_single_user(
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session)
    result = await svc.get_thesis_detail(_default_user_id(), thesis_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Thesis {thesis_id} not found")
    return result


# ---------------------------------------------------------------------------
# 4. Upcoming catalysts
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/catalysts/upcoming")
async def get_upcoming_catalysts(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> dict[str, Any]:
    svc = DashboardService(session)
    return _paginated(await svc.get_upcoming_catalysts(user_id, days=days))


@router.get("/dashboard/catalysts/upcoming")
async def get_upcoming_catalysts_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> dict[str, Any]:
    svc = DashboardService(session)
    return _paginated(await svc.get_upcoming_catalysts(_default_user_id(), days=days))


# ---------------------------------------------------------------------------
# 5. Latest scan snapshot
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/scan/latest")
async def get_scan_latest(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any] | None:
    return await _ensure_scan_snapshot(session, user_id)


@router.get("/dashboard/scan/latest")
async def get_scan_latest_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any] | None:
    return await _ensure_scan_snapshot(session, _default_user_id())


# ---------------------------------------------------------------------------
# 6. Latest brief snapshot
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/brief/latest")
async def get_brief_latest(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    phase: Annotated[Literal["morning", "eod"], Query()] = "morning",
) -> dict[str, Any] | None:
    svc = DashboardService(session)
    return await svc.get_brief_latest(user_id, phase=phase)


@router.get("/dashboard/brief/latest")
async def get_brief_latest_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    phase: Annotated[Literal["morning", "eod"], Query()] = "morning",
) -> dict[str, Any] | None:
    svc = DashboardService(session)
    return await svc.get_brief_latest(_default_user_id(), phase=phase)


# ---------------------------------------------------------------------------
# 7. Backtesting — verdict accuracy
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/backtesting/verdict-accuracy")
async def get_verdict_accuracy(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session)
    return _paginated(await svc.get_verdict_accuracy(user_id))


@router.get("/dashboard/backtesting/verdict-accuracy")
async def get_verdict_accuracy_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session)
    return _paginated(await svc.get_verdict_accuracy(_default_user_id()))


# ---------------------------------------------------------------------------
# 8. Backtesting — thesis performances
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/backtesting/thesis-performances")
async def get_thesis_performances(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    ticker: Annotated[str | None, Query(description="Filter theo ticker")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    svc = DashboardService(session)
    return await svc.get_thesis_performances(user_id, ticker=ticker, limit=limit)


@router.get("/dashboard/backtesting/thesis-performances")
async def get_thesis_performances_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    ticker: Annotated[str | None, Query(description="Filter theo ticker")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    svc = DashboardService(session)
    return await svc.get_thesis_performances(
        _default_user_id(),
        ticker=ticker,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# 9. Backtesting — price snapshots
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/backtesting/price-snapshots/{thesis_id}")
async def get_price_snapshots(
    user_id: str,
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session)
    result = await svc.get_price_snapshots(user_id, thesis_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Thesis {thesis_id} not found")
    return result


@router.get("/dashboard/backtesting/price-snapshots/{thesis_id}")
async def get_price_snapshots_single_user(
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session)
    result = await svc.get_price_snapshots(_default_user_id(), thesis_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Thesis {thesis_id} not found")
    return result


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


@router.get("/leaderboard/{user_id}", response_model=LeaderboardResponse)
async def get_leaderboard(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    sort_by: Annotated[Literal["score", "pnl"], Query()] = "score",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> LeaderboardResponse:
    svc = LeaderboardService(session)
    return await svc.get_leaderboard(user_id, sort_by=sort_by, limit=limit)


@router.get("/leaderboard", response_model=LeaderboardResponse)
async def get_leaderboard_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    sort_by: Annotated[Literal["score", "pnl"], Query()] = "score",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> LeaderboardResponse:
    svc = LeaderboardService(session)
    return await svc.get_leaderboard(_default_user_id(), sort_by=sort_by, limit=limit)


# ---------------------------------------------------------------------------
# Thesis timeline
# ---------------------------------------------------------------------------


@router.get("/thesis/{thesis_id}/timeline", response_model=ThesisTimelineResponse)
async def get_thesis_timeline(
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ThesisTimelineResponse:
    svc = ThesisTimelineService(session)
    result = await svc.get_timeline(thesis_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Thesis {thesis_id} not found")
    return result
