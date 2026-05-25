"""Dashboard readmodel routes — read-only projection endpoints for the frontend.

Owner: api segment (thin adapter).
All business/query logic lives in readmodel.dashboard_service or
the segment-specific services it delegates to.

Single-user mode:
- If USER_ID env var is set, alias endpoints without /{user_id}
  will automatically use that user id.

Route ordering rule (FastAPI matches in declaration order):
  Static/literal path segments MUST be declared before parameterised ones.
  e.g. /dashboard/theses/aggregate must come before /dashboard/theses/{thesis_id}
  otherwise FastAPI casts "aggregate" -> int and returns 422.
"""

from __future__ import annotations

import os
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.db import get_db_session as get_db
from src.platform.logging import get_logger
from src.readmodel.dashboard_service import DashboardService

logger = get_logger(__name__)

# NOTE: app.py mounts this router with prefix="/api/v1".
# This prefix must stay "/readmodel" so routes resolve to /api/v1/readmodel/...
router = APIRouter(prefix="/readmodel", tags=["readmodel"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_user_id() -> str:
    """Single-user mode: read USER_ID from env (set at startup)."""
    uid = os.environ.get("USER_ID", "")
    if not uid:
        raise RuntimeError("USER_ID env var not set — cannot resolve default user.")
    return uid


def get_quote_service():  # noqa: ANN202
    from src.market.quote_service import get_quote_service as _qs
    return _qs()


async def _build_price_map(tickers: list[str]) -> dict[str, float]:
    """Fetch current prices cho danh sach tickers tu QuoteService."""
    if not tickers:
        return {}
    try:
        qs = get_quote_service()
        quotes = await qs.get_quotes(tickers)
        return {q.ticker: q.close for q in quotes if q.close is not None}
    except Exception:
        return {}


async def _load_positions_map(session: AsyncSession, user_id: str) -> dict[str, tuple[float, float]]:
    from sqlalchemy import select
    from src.portfolio.models import Position
    rows = (
        await session.execute(
            select(Position.ticker, Position.qty, Position.avg_cost)
            .where(
                Position.user_id == user_id,
                Position.closed_at.is_(None),
                Position.qty > 0,
            )
        )
    ).all()
    result: dict[str, tuple[float, float]] = {}
    for r in rows:
        if r.ticker not in result:
            result[r.ticker] = (r.qty, r.avg_cost)
    return result


async def _resolve_thesis_ticker(session: AsyncSession, thesis_id: int) -> str | None:
    """Resolve ticker for a thesis_id. Returns None if thesis not found."""
    from sqlalchemy import select
    from src.thesis.models import Thesis
    result = await session.execute(select(Thesis.ticker).where(Thesis.id == thesis_id))
    return result.scalar_one_or_none()


async def _ensure_scan_snapshot(
    session: AsyncSession,
    user_id: str,
) -> dict[str, Any] | None:
    svc = DashboardService(session)
    latest = await svc.get_scan_latest(user_id)
    if latest is not None:
        return latest
    from src.watchlist.scan_service import ScanService
    scan_svc = ScanService(session=session, quote_service=get_quote_service())
    await scan_svc.scan_user_if_stale(user_id=user_id, max_age_minutes=30)
    await session.commit()
    return await svc.get_scan_latest(user_id)


# ---------------------------------------------------------------------------
# 1. Stats
# ---------------------------------------------------------------------------

@router.get("/dashboard/stats")
async def get_stats(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    return await svc.get_stats(user_id=_default_user_id())


# ---------------------------------------------------------------------------
# 2. Theses list
# ---------------------------------------------------------------------------

@router.get("/dashboard/{user_id}/theses")
async def get_theses(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    status: str = Query(default="active", description="Filter by thesis status"),
    limit: int = Query(default=100, ge=1, le=500),
    ticker: str | None = Query(default=None, description="Filter by ticker"),
    enrich_prices: bool = Query(default=True, description="Fetch giá hiện tại + avg_cost từ positions để tính P&L"),
) -> dict[str, Any]:
    from src.readmodel.thesis_query_service import ThesisQueryService
    thesis_svc = ThesisQueryService(session=session)
    items = await thesis_svc.get_theses_list(
        user_id=user_id,
        status=status,
        limit=limit,
        ticker=ticker,
    )
    if enrich_prices and items:
        tickers = list({t["ticker"] for t in items if t.get("ticker")})
        price_map: dict[str, float] = {}
        if tickers:
            try:
                qs = get_quote_service()
                quotes = await qs.get_quotes(tickers)
                price_map = {q.ticker: q.close for q in quotes if q.close is not None}
            except Exception as exc:
                logger.warning("readmodel.get_theses.price_fetch_failed", error=str(exc))
        pos_map = await _load_positions_map(session, user_id)
        for item in items:
            t = item["ticker"]
            item["current_price"] = price_map.get(t)
            pos = pos_map.get(t)
            item["qty"] = pos[0] if pos else None
            item["avg_cost"] = pos[1] if pos else None
            ep = item.get("avg_cost") or item.get("entry_price")
            cp = item.get("current_price")
            if ep and cp and ep > 0:
                item["pnl_pct"] = round((cp - ep) / ep * 100, 2)
            else:
                item["pnl_pct"] = None
    return {"items": items, "total": len(items)}


@router.get("/dashboard/theses")
async def get_theses_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    status: str = Query(default="active"),
    limit: int = Query(default=100, ge=1, le=500),
    ticker: str | None = Query(default=None),
    enrich_prices: bool = Query(default=True),
) -> dict[str, Any]:
    return await get_theses(
        user_id=_default_user_id(),
        session=session,
        status=status,
        limit=limit,
        ticker=ticker,
        enrich_prices=enrich_prices,
    )


# ---------------------------------------------------------------------------
# 3. Thesis detail
# ---------------------------------------------------------------------------

@router.get("/dashboard/{user_id}/thesis/{thesis_id}")
async def get_thesis_detail(
    user_id: str,
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    result = await svc.get_thesis_detail(user_id=user_id, thesis_id=thesis_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Thesis not found")
    return result


@router.get("/dashboard/thesis/{thesis_id}")
async def get_thesis_detail_default_user(
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    return await get_thesis_detail(
        user_id=_default_user_id(),
        thesis_id=thesis_id,
        session=session,
    )


# ---------------------------------------------------------------------------
# 4. Upcoming catalysts
# IMPORTANT: /dashboard/catalysts must be declared BEFORE /dashboard/{user_id}/...
# to avoid FastAPI treating "catalysts" as a user_id path param.
# ---------------------------------------------------------------------------

@router.get("/dashboard/catalysts/upcoming")
async def get_catalysts_upcoming_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    items = await svc.get_upcoming_catalysts(user_id=_default_user_id(), days=days)
    return {"items": items}


@router.get("/dashboard/{user_id}/catalysts/upcoming")
async def get_catalysts_upcoming(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    items = await svc.get_upcoming_catalysts(user_id=user_id, days=days)
    return {"items": items}


# ---------------------------------------------------------------------------
# 5. Thesis portfolio aggregate
# IMPORTANT: /dashboard/portfolio/thesis-aggregate before /dashboard/{user_id}/...
# ---------------------------------------------------------------------------

@router.get("/dashboard/theses/aggregate")
async def get_thesis_aggregate_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    return await get_thesis_portfolio_aggregate(
        user_id=_default_user_id(), session=session
    )


@router.get("/dashboard/{user_id}/theses/aggregate")
async def get_thesis_portfolio_aggregate(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    return await svc.get_thesis_portfolio_aggregate(user_id=user_id)


# ---------------------------------------------------------------------------
# 6. Conviction timeline (dashboard-level, across all theses)
# ---------------------------------------------------------------------------

@router.get("/dashboard/{user_id}/conviction-timeline")
async def get_conviction_timeline_dashboard(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    items = await svc.get_conviction_timeline(user_id=user_id, limit=limit)
    return {"items": items}


@router.get("/dashboard/conviction-timeline")
async def get_conviction_timeline_dashboard_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    return await get_conviction_timeline_dashboard(
        user_id=_default_user_id(), session=session, limit=limit
    )


# ---------------------------------------------------------------------------
# 7. Scan latest
# ---------------------------------------------------------------------------

@router.get("/dashboard/scan/latest")
async def get_scan_latest_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    return await _ensure_scan_snapshot(session, _default_user_id()) or {}


@router.get("/dashboard/{user_id}/scan/latest")
async def get_scan_latest(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    return await _ensure_scan_snapshot(session, user_id) or {}


# ---------------------------------------------------------------------------
# 8. Brief latest
# ---------------------------------------------------------------------------

@router.get("/dashboard/brief/latest")
async def get_brief_latest_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    phase: str = Query(default="morning"),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    result = await svc.get_brief_latest(user_id=_default_user_id(), phase=phase)
    return result or {}


@router.get("/dashboard/{user_id}/brief/latest")
async def get_brief_latest(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    phase: str = Query(default="morning"),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    result = await svc.get_brief_latest(user_id=user_id, phase=phase)
    return result or {}


# ---------------------------------------------------------------------------
# 9. Brief feedback summary
# ---------------------------------------------------------------------------

@router.get("/dashboard/brief/feedback-summary")
async def get_brief_feedback_summary_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    days: Annotated[int, Query(ge=1, le=90, description="Window tính acted_rate (ngày)")] = 30,
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    return await svc.get_brief_feedback_summary(user_id=_default_user_id(), days=days)


@router.get("/dashboard/{user_id}/brief/feedback-summary")
async def get_brief_feedback_summary(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    days: Annotated[int, Query(ge=1, le=90, description="Window tính acted_rate (ngày)")] = 30,
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    return await svc.get_brief_feedback_summary(user_id=user_id, days=days)


# ---------------------------------------------------------------------------
# 10. Acted tickers recent
# ---------------------------------------------------------------------------

@router.get("/dashboard/acted-tickers")
async def get_acted_tickers_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    tickers = await svc.get_acted_tickers_recent(user_id=_default_user_id(), days=days)
    return {"tickers": tickers}


@router.get("/dashboard/{user_id}/acted-tickers")
async def get_acted_tickers(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    tickers = await svc.get_acted_tickers_recent(user_id=user_id, days=days)
    return {"tickers": tickers}


# ---------------------------------------------------------------------------
# 11. Triggered alerts
# ---------------------------------------------------------------------------

@router.get("/dashboard/alerts/triggered")
async def get_triggered_alerts_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    items = await svc.get_triggered_alerts(user_id=_default_user_id(), limit=limit)
    return {"items": items}


@router.get("/dashboard/{user_id}/alerts/triggered")
async def get_triggered_alerts(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    items = await svc.get_triggered_alerts(user_id=user_id, limit=limit)
    return {"items": items}


# ---------------------------------------------------------------------------
# 12. Recent signals
# ---------------------------------------------------------------------------

@router.get("/dashboard/signals/recent")
async def get_recent_signals_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    ticker: str | None = Query(default=None),
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    items = await svc.get_recent_signals(
        user_id=_default_user_id(), ticker=ticker, days=days, limit=limit
    )
    return {"items": items}


@router.get("/dashboard/{user_id}/signals/recent")
async def get_recent_signals(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    ticker: Annotated[
        str | None,
        Query(description="Filter theo mã cụ thể (VD: VCB). Bỏ qua để lấy toàn bộ watchlist."),
    ] = None,
    days: Annotated[int, Query(ge=1, le=90, description="Window thời gian (ngày)")] = 7,
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    items = await svc.get_recent_signals(user_id=user_id, ticker=ticker, days=days, limit=limit)
    return {"items": items}


# ---------------------------------------------------------------------------
# 13. Backtesting — verdict accuracy + thesis performances + price snapshots
# IMPORTANT: static sub-paths (/backtesting/...) before /{user_id}/...
# ---------------------------------------------------------------------------

@router.get("/dashboard/backtesting/verdict-accuracy")
async def get_verdict_accuracy_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    items = await svc.get_verdict_accuracy(user_id=_default_user_id())
    return {"items": items}


@router.get("/dashboard/{user_id}/backtesting/verdict-accuracy")
async def get_verdict_accuracy(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    items = await svc.get_verdict_accuracy(user_id=user_id)
    return {"items": items}


@router.get("/dashboard/backtesting/thesis-performances")
async def get_thesis_performances_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    ticker: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    items = await svc.get_thesis_performances(
        user_id=_default_user_id(), ticker=ticker, limit=limit
    )
    return {"items": items}


@router.get("/dashboard/{user_id}/backtesting/thesis-performances")
async def get_thesis_performances(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    ticker: Annotated[str | None, Query(description="Filter theo ticker")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    items = await svc.get_thesis_performances(user_id=user_id, ticker=ticker, limit=limit)
    return {"items": items}


@router.get("/dashboard/backtesting/price-snapshots/{thesis_id}")
async def get_price_snapshots_default_user(
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    return await get_price_snapshots(
        user_id=_default_user_id(), thesis_id=thesis_id, session=session
    )


@router.get("/dashboard/{user_id}/backtesting/price-snapshots/{thesis_id}")
async def get_price_snapshots(
    user_id: str,
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    result = await svc.get_price_snapshots(user_id=user_id, thesis_id=thesis_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Thesis {thesis_id} not found")
    return result


# ---------------------------------------------------------------------------
# 14. Portfolio — /dashboard/portfolio/trades  (PnlService)
#               + /dashboard/portfolio         (DashboardService)
# IMPORTANT: /portfolio/trades before /portfolio to avoid path ambiguity.
# ---------------------------------------------------------------------------

from src.portfolio.pnl_service import PnlService  # noqa: E402


@router.get("/dashboard/portfolio/trades")
async def get_portfolio_trades_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    return await get_portfolio_trades(user_id=_default_user_id(), session=session)


@router.get("/dashboard/{user_id}/portfolio/trades")
async def get_portfolio_trades(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
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
                # thesis_id: forward to frontend so QuickTrade can pre-select the linked
                # thesis in the dropdown (Trades tab only — Thesis tab uses p.id directly).
                "thesis_id": p.thesis_id,
                # thesis_status (Gap 3 B2): enables portfolio-renderer.js to render a
                # warning badge on rows where the linked thesis has been invalidated/closed.
                "thesis_status": p.thesis_status,
            }
            for p in pnl.positions
        ],
        "total_unrealized_pnl": pnl.total_unrealized_pnl,
        "total_unrealized_pct": pnl.total_unrealized_pct,
        "total_cost_basis": pnl.total_cost_basis,
        "total_market_value": pnl.total_market_value,
        "errors": pnl.errors,
    }


@router.get("/dashboard/portfolio")
async def get_portfolio_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    return await get_portfolio(user_id=_default_user_id(), session=session)


@router.get("/dashboard/{user_id}/portfolio")
async def get_portfolio(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    return await svc.get_portfolio(
        user_id=user_id,
        quote_service=get_quote_service(),
    )


# ---------------------------------------------------------------------------
# 15. Attention Panel — "Việc cần làm hôm nay"
# IMPORTANT: /dashboard/attention before /dashboard/{user_id}/attention
# ---------------------------------------------------------------------------

@router.get("/dashboard/attention")
async def get_attention_needed_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    enrich_prices: bool = Query(default=True),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict[str, Any]:
    return await get_attention_needed(
        user_id=_default_user_id(), session=session, enrich_prices=enrich_prices, limit=limit
    )


@router.get("/dashboard/{user_id}/attention")
async def get_attention_needed(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    enrich_prices: Annotated[
        bool,
        Query(description="Fetch live prices để kiểm tra stop_loss proximity."),
    ] = True,
    limit: Annotated[int, Query(ge=1, le=50, description="Số attention items tối đa")] = 20,
) -> dict[str, Any]:
    price_map: dict[str, float] = {}
    if enrich_prices:
        svc_pre = DashboardService(session)
        active_theses = await svc_pre.get_theses_list(user_id, status="active", limit=500)
        tickers = list({t["ticker"] for t in active_theses if t.get("ticker")})
        price_map = await _build_price_map(tickers)
    svc = DashboardService(session=session)
    return await svc.get_attention_needed(user_id=user_id, price_map=price_map, limit=limit)


# ---------------------------------------------------------------------------
# 16. Leaderboard
# ---------------------------------------------------------------------------

@router.get("/leaderboard")
async def get_leaderboard_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    sort_by: Annotated[Literal["score", "pnl"], Query()] = "score",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    return await get_leaderboard(
        user_id=_default_user_id(), session=session, sort_by=sort_by, limit=limit
    )


@router.get("/leaderboard/{user_id}")
async def get_leaderboard(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    sort_by: Annotated[Literal["score", "pnl"], Query()] = "score",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    from src.readmodel.leaderboard_service import LeaderboardService
    svc = LeaderboardService(session)
    return await svc.get_leaderboard(user_id, sort_by=sort_by, limit=limit)


# ---------------------------------------------------------------------------
# 17. Thesis timeline — general event log
# ---------------------------------------------------------------------------

@router.get("/thesis/{thesis_id}/timeline")
async def get_thesis_timeline(
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    from src.readmodel.timeline_service import ThesisTimelineService
    svc = ThesisTimelineService(session)
    result = await svc.get_timeline(thesis_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Thesis {thesis_id} not found")
    return result


# ---------------------------------------------------------------------------
# 18. Review Timeline — N AI reviews gần nhất của một thesis
# ---------------------------------------------------------------------------

@router.get("/thesis/{thesis_id}/review-timeline")
async def get_review_timeline(
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[
        int,
        Query(ge=1, le=20, description="Số AI reviews gần nhất trả về (mới nhất trước)"),
    ] = 5,
) -> dict[str, Any]:
    from src.readmodel.timeline_service import ThesisTimelineService
    svc = ThesisTimelineService(session)
    result = await svc.get_review_timeline(thesis_id, limit=limit)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Thesis {thesis_id} not found")
    return result


# ---------------------------------------------------------------------------
# 19. Conviction Score Timeline — per-thesis, with live price injection
# ---------------------------------------------------------------------------

@router.get("/thesis/{thesis_id}/conviction-timeline")
async def get_conviction_timeline(
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100, description="Số data-point tối đa trả về")] = 20,
    enrich_price: Annotated[
        bool,
        Query(
            description=(
                "Fetch live price từ QuoteService để inject vào điểm cuối cùng "
                "(Option C — fallback khi AI review chạy trước market snapshot job)."
            )
        ),
    ] = True,
) -> dict[str, Any]:
    """Conviction score timeline cho một thesis."""
    current_price: float | None = None
    if enrich_price:
        ticker = await _resolve_thesis_ticker(session, thesis_id)
        if ticker:
            price_map = await _build_price_map([ticker])
            current_price = price_map.get(ticker)

    from src.readmodel.timeline_service import ThesisTimelineService
    svc = ThesisTimelineService(session)
    result = await svc.get_conviction_timeline(
        thesis_id,
        limit=limit,
        current_price=current_price,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"Thesis {thesis_id} not found")
    return result
