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
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.bootstrap import get_quote_service
from src.platform.config import settings
from src.api.deps import get_db
from src.readmodel.dashboard_service import DashboardService
from src.readmodel.leaderboard_service import LeaderboardService
from src.readmodel.schemas import (
    ConvictionTimelineResponse,
    LeaderboardResponse,
    ThesisTimelineResponse,
)
from src.readmodel.timeline_service import ThesisTimelineService
from src.portfolio.pnl_service import PnlService
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
    # ScanService._persist_snapshot() only calls session.add() — the scheduler
    # commits its own transaction. On the HTTP on-demand path, we must commit
    # here so the follow-up get_scan_latest() query can see the new row.
    await session.commit()
    return await svc.get_scan_latest(user_id)


async def _build_price_map(tickers: list[str]) -> dict[str, float]:
    """Fetch current prices cho danh sach tickers tu QuoteService.
    Tra ve {} neu QuoteService unavailable hoac tickers rong.
    """
    if not tickers:
        return {}
    try:
        quote_svc = get_quote_service()
        quotes = await quote_svc.get_bulk_quotes(tickers)
        return {q.ticker: q.price for q in quotes if q.price}
    except Exception:
        return {}


async def _build_position_map(
    session: AsyncSession, user_id: str
) -> dict[str, tuple[float, float]]:
    """Load open positions for user -> {ticker: (qty, avg_cost)}.
    Returns {} on error or no positions.
    """
    try:
        from src.portfolio.models import Position

        rows = (
            await session.execute(
                select(Position.ticker, Position.qty, Position.avg_cost).where(
                    Position.user_id == user_id,
                    Position.closed_at.is_(None),
                    Position.qty > 0,
                )
            )
        ).all()
        result: dict[str, tuple[float, float]] = {}
        for p in rows:
            if p.ticker not in result:
                result[p.ticker] = (p.qty, p.avg_cost)
        return result
    except Exception:
        return {}


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
# 2. Theses list — enriched with live price + avg_cost from positions
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/theses")
async def get_theses_list(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    status: Annotated[str, Query()] = "active",
    ticker: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    enrich_prices: Annotated[
        bool,
        Query(description="Fetch giá hiện tại + avg_cost từ positions để tính P&L"),
    ] = True,
) -> dict[str, Any]:
    svc = DashboardService(session)

    price_map: dict[str, float] = {}
    position_map: dict[str, tuple[float, float]] = {}

    if enrich_prices:
        # Lấy tickers trước (lightweight query không enrich)
        raw_items = await svc.get_theses_list(user_id, status=status, ticker=ticker, limit=limit)
        tickers = list({t["ticker"] for t in raw_items if t.get("ticker")})
        price_map, position_map = await _fetch_price_and_position(
            session=session, user_id=user_id, tickers=tickers
        )

    items = await svc.get_theses_list(
        user_id,
        status=status,
        ticker=ticker,
        limit=limit,
        price_map=price_map,
        position_map=position_map,
    )
    return _paginated(items)


@router.get("/dashboard/theses")
async def get_theses_list_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    status: Annotated[str, Query()] = "active",
    ticker: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    enrich_prices: Annotated[
        bool,
        Query(description="Fetch giá hiện tại + avg_cost từ positions để tính P&L"),
    ] = True,
) -> dict[str, Any]:
    return await get_theses_list(
        user_id=_default_user_id(),
        session=session,
        status=status,
        ticker=ticker,
        limit=limit,
        enrich_prices=enrich_prices,
    )


async def _fetch_price_and_position(
    session: AsyncSession,
    user_id: str,
    tickers: list[str],
) -> tuple[dict[str, float], dict[str, tuple[float, float]]]:
    """Parallel-ish fetch: price_map + position_map."""
    import asyncio

    price_map, position_map = await asyncio.gather(
        _build_price_map(tickers),
        _build_position_map(session, user_id),
    )
    return price_map, position_map


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
# 5. Thesis portfolio aggregate
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/theses/aggregate")
async def get_thesis_aggregate(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    enrich_prices: Annotated[
        bool,
        Query(description="Fetch live price + position map để tính P&L aggregate"),
    ] = True,
) -> dict[str, Any]:
    """Thesis portfolio aggregate — counts + P&L totals + breakdowns."""
    svc = DashboardService(session)

    price_map: dict[str, float] = {}
    position_map: dict[str, tuple[float, float]] = {}

    if enrich_prices:
        raw_items = await svc.get_theses_list(user_id, status="active", limit=500)
        tickers = list({t["ticker"] for t in raw_items if t.get("ticker")})
        price_map, position_map = await _fetch_price_and_position(
            session=session, user_id=user_id, tickers=tickers
        )

    return await svc.get_thesis_portfolio_aggregate(
        user_id,
        price_map=price_map,
        position_map=position_map,
    )


@router.get("/dashboard/theses/aggregate")
async def get_thesis_aggregate_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    enrich_prices: Annotated[
        bool,
        Query(description="Fetch live price + position map để tính P&L aggregate"),
    ] = True,
) -> dict[str, Any]:
    return await get_thesis_aggregate(
        user_id=_default_user_id(),
        session=session,
        enrich_prices=enrich_prices,
    )


# ---------------------------------------------------------------------------
# 6. Latest scan snapshot
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
# 7. Brief snapshots + feedback
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


@router.get("/dashboard/{user_id}/brief/feedback-summary")
async def get_brief_feedback_summary(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    days: Annotated[int, Query(ge=1, le=90, description="Window tính acted_rate (ngày)")] = 30,
) -> dict[str, Any]:
    svc = DashboardService(session)
    return await svc.get_brief_feedback_summary(user_id, days=days)


@router.get("/dashboard/brief/feedback-summary")
async def get_brief_feedback_summary_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    days: Annotated[int, Query(ge=1, le=90, description="Window tính acted_rate (ngày)")] = 30,
) -> dict[str, Any]:
    svc = DashboardService(session)
    return await svc.get_brief_feedback_summary(_default_user_id(), days=days)


# ---------------------------------------------------------------------------
# 8. Triggered alerts — panel "Alerts cần xử lý"
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/alerts/triggered")
async def get_triggered_alerts(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200, description="Số alert tối đa trả về")] = 50,
) -> dict[str, Any]:
    """Alerts đã fire (status=TRIGGERED), chưa được dismiss/reactivate.

    Response shape: {items: [...], total: N}
    """
    svc = DashboardService(session)
    return _paginated(await svc.get_triggered_alerts(user_id, limit=limit))


@router.get("/dashboard/alerts/triggered")
async def get_triggered_alerts_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200, description="Số alert tối đa trả về")] = 50,
) -> dict[str, Any]:
    svc = DashboardService(session)
    return _paginated(await svc.get_triggered_alerts(_default_user_id(), limit=limit))


# ---------------------------------------------------------------------------
# 9. Recent signal events — context kỹ thuật per ticker
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/signals/recent")
async def get_recent_signals(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    ticker: Annotated[
        str | None,
        Query(description="Filter theo mã cụ thể (VD: VCB). Bỏ qua để lấy toàn bộ watchlist."),
    ] = None,
    days: Annotated[int, Query(ge=1, le=90, description="Window thời gian (ngày)")] = 7,
    limit: Annotated[int, Query(ge=1, le=200, description="Số signal tối đa trả về")] = 50,
) -> dict[str, Any]:
    """Signal events gần đây (MA crossover, volume spike, RSI oversold, v.v.).

    Response shape: {items: [...], total: N}

    Mỗi item:
        id           — int
        event_id     — str  (unique dedup key)
        ticker       — str
        signal_type  — str  (VD: "ma_crossover", "volume_spike", "rsi_oversold")
        strength     — float 0.0-1.0
        confidence   — float 0.0-1.0
        source       — str  ("technical" | "ai" | ...)
        metadata     — dict | null
        occurred_at  — ISO str
        processed_at — ISO str | null
    """
    svc = DashboardService(session)
    return _paginated(
        await svc.get_recent_signals(user_id, ticker=ticker, days=days, limit=limit)
    )


@router.get("/dashboard/signals/recent")
async def get_recent_signals_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    ticker: Annotated[
        str | None,
        Query(description="Filter theo mã cụ thể (VD: VCB). Bỏ qua để lấy toàn bộ watchlist."),
    ] = None,
    days: Annotated[int, Query(ge=1, le=90, description="Window thời gian (ngày)")] = 7,
    limit: Annotated[int, Query(ge=1, le=200, description="Số signal tối đa trả về")] = 50,
) -> dict[str, Any]:
    svc = DashboardService(session)
    return _paginated(
        await svc.get_recent_signals(_default_user_id(), ticker=ticker, days=days, limit=limit)
    )


# ---------------------------------------------------------------------------
# 10. Backtesting — verdict accuracy
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
# 11. Backtesting — thesis performances
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
# 12. Backtesting — price snapshots
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
# 13. Portfolio — Trades view (PnlService — positions thực tế từ DB)
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/portfolio/trades")
async def get_portfolio_trades(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Tab Trades: lay positions thuc te tu bang positions + live price tu QuoteService."""
    svc = PnlService(session=session, quote_service=get_quote_service())
    pnl = await svc.get_portfolio_pnl(user_id)
    return {
        "positions": [
            {
                "ticker": p.ticker,
                "qty": p.qty,
                "avg_cost": p.avg_cost,
                "current_price": p.current_price,
                "cost_basis": p.cost_basis,
                "market_value": p.market_value,
                "unrealized_pnl": p.unrealized_pnl,
                "unrealized_pct": p.unrealized_pct,
            }
            for p in pnl.positions
        ],
        "total_unrealized_pnl": pnl.total_unrealized_pnl,
        "total_unrealized_pct": pnl.total_unrealized_pct,
        "total_cost_basis": pnl.total_cost_basis,
        "total_market_value": pnl.total_market_value,
        "errors": pnl.errors,
    }


@router.get("/dashboard/portfolio/trades")
async def get_portfolio_trades_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    return await get_portfolio_trades(
        user_id=_default_user_id(),
        session=session,
    )


# ---------------------------------------------------------------------------
# 14. Portfolio — Thesis view (DashboardService — thesis-based positions)
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/portfolio")
async def get_portfolio(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    enrich_prices: Annotated[
        bool,
        Query(description="Fetch gia hien tai tu QuoteService de tinh P&L realtime"),
    ] = True,
) -> dict[str, Any]:
    """Thesis portfolio view — thesis active + aggregate P&L."""
    svc = DashboardService(session)

    price_map: dict[str, float] = {}
    if enrich_prices:
        theses = await svc.get_theses_list(user_id, status="active", limit=500)
        tickers = list({t["ticker"] for t in theses if t.get("ticker")})
        price_map = await _build_price_map(tickers)

    return await svc.get_portfolio(user_id, price_map=price_map)


@router.get("/dashboard/portfolio")
async def get_portfolio_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    enrich_prices: Annotated[
        bool,
        Query(description="Fetch gia hien tai tu QuoteService de tinh P&L realtime"),
    ] = True,
) -> dict[str, Any]:
    return await get_portfolio(
        user_id=_default_user_id(),
        session=session,
        enrich_prices=enrich_prices,
    )


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
# Thesis timeline — general event log
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


# ---------------------------------------------------------------------------
# Conviction Score Timeline
# ---------------------------------------------------------------------------


@router.get("/thesis/{thesis_id}/conviction-timeline", response_model=ConvictionTimelineResponse)
async def get_conviction_timeline(
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100, description="Số data-point tối đa trả về")] = 20,
) -> ConvictionTimelineResponse:
    svc = ThesisTimelineService(session)
    result = await svc.get_conviction_timeline(thesis_id, limit=limit)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Thesis {thesis_id} not found")
    return result
