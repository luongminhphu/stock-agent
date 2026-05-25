"""Dashboard readmodel routes — read-only projection endpoints for the frontend.

Owner: api segment (thin adapter).
All business/query logic lives in readmodel.dashboard_service or
the segment-specific services it delegates to.
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.db import get_db
from src.platform.logging import get_logger
from src.readmodel.dashboard_service import DashboardService

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/readmodel", tags=["readmodel"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_user_id() -> str:
    """Single-user mode: read USER_ID from env (set at startup)."""
    uid = os.environ.get("USER_ID", "")
    if not uid:
        raise RuntimeError("USER_ID env var not set — cannot resolve default user.")
    return uid


async def _get_service(session: AsyncSession) -> DashboardService:
    from src.market.quote_service import get_quote_service
    from src.readmodel.dashboard_service import DashboardService
    return DashboardService(session=session)


def _get_quote_service():  # noqa: ANN202
    from src.market.quote_service import get_quote_service
    return get_quote_service()


def get_quote_service():  # noqa: ANN202
    from src.market.quote_service import get_quote_service as _qs
    return _qs()


# ---------------------------------------------------------------------------
# Utility — open positions map
# ---------------------------------------------------------------------------

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
        from fastapi import HTTPException
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
# ---------------------------------------------------------------------------

@router.get("/dashboard/{user_id}/catalysts")
async def get_catalysts(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    items = await svc.get_upcoming_catalysts(user_id=user_id, days=days)
    return {"items": items}


@router.get("/dashboard/catalysts")
async def get_catalysts_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    return await get_catalysts(user_id=_default_user_id(), session=session, days=days)


# ---------------------------------------------------------------------------
# 5. Thesis portfolio aggregate
# ---------------------------------------------------------------------------

@router.get("/dashboard/{user_id}/portfolio/thesis-aggregate")
async def get_thesis_portfolio_aggregate(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    return await svc.get_thesis_portfolio_aggregate(user_id=user_id)


@router.get("/dashboard/portfolio/thesis-aggregate")
async def get_thesis_portfolio_aggregate_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    return await get_thesis_portfolio_aggregate(
        user_id=_default_user_id(), session=session
    )


# ---------------------------------------------------------------------------
# 6. Conviction timeline
# ---------------------------------------------------------------------------

@router.get("/dashboard/{user_id}/conviction-timeline")
async def get_conviction_timeline(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    items = await svc.get_conviction_timeline(user_id=user_id, limit=limit)
    return {"items": items}


@router.get("/dashboard/conviction-timeline")
async def get_conviction_timeline_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    return await get_conviction_timeline(
        user_id=_default_user_id(), session=session, limit=limit
    )


# ---------------------------------------------------------------------------
# 7. Scan latest
# ---------------------------------------------------------------------------

@router.get("/dashboard/{user_id}/scan/latest")
async def get_scan_latest(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    result = await svc.get_scan_latest(user_id=user_id)
    return result or {}


@router.get("/dashboard/scan/latest")
async def get_scan_latest_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    return await get_scan_latest(user_id=_default_user_id(), session=session)


# ---------------------------------------------------------------------------
# 8. Brief latest
# ---------------------------------------------------------------------------

@router.get("/dashboard/{user_id}/brief/latest")
async def get_brief_latest(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    phase: str = Query(default="morning"),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    result = await svc.get_brief_latest(user_id=user_id, phase=phase)
    return result or {}


@router.get("/dashboard/brief/latest")
async def get_brief_latest_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    phase: str = Query(default="morning"),
) -> dict[str, Any]:
    return await get_brief_latest(
        user_id=_default_user_id(), session=session, phase=phase
    )


# ---------------------------------------------------------------------------
# 9. Brief feedback summary
# ---------------------------------------------------------------------------

@router.get("/dashboard/{user_id}/brief/feedback-summary")
async def get_brief_feedback_summary(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    return await svc.get_brief_feedback_summary(user_id=user_id, limit=limit)


@router.get("/dashboard/brief/feedback-summary")
async def get_brief_feedback_summary_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    return await get_brief_feedback_summary(
        user_id=_default_user_id(), session=session, limit=limit
    )


# ---------------------------------------------------------------------------
# 10. Acted tickers recent
# ---------------------------------------------------------------------------

@router.get("/dashboard/{user_id}/acted-tickers")
async def get_acted_tickers(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    tickers = await svc.get_acted_tickers_recent(user_id=user_id, days=days)
    return {"tickers": tickers}


@router.get("/dashboard/acted-tickers")
async def get_acted_tickers_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    return await get_acted_tickers(
        user_id=_default_user_id(), session=session, days=days
    )


# ---------------------------------------------------------------------------
# 11. Triggered alerts
# ---------------------------------------------------------------------------

@router.get("/dashboard/{user_id}/alerts/triggered")
async def get_triggered_alerts(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    items = await svc.get_triggered_alerts(user_id=user_id, limit=limit)
    return {"items": items}


@router.get("/dashboard/alerts/triggered")
async def get_triggered_alerts_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    return await get_triggered_alerts(
        user_id=_default_user_id(), session=session, limit=limit
    )


# ---------------------------------------------------------------------------
# 12. Recent signals
# ---------------------------------------------------------------------------

@router.get("/dashboard/{user_id}/signals/recent")
async def get_recent_signals(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    items = await svc.get_recent_signals(user_id=user_id, limit=limit)
    return {"items": items}


@router.get("/dashboard/signals/recent")
async def get_recent_signals_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    return await get_recent_signals(
        user_id=_default_user_id(), session=session, limit=limit
    )


# ---------------------------------------------------------------------------
# 13. Verdict accuracy + thesis performances + price snapshots
# ---------------------------------------------------------------------------

@router.get("/dashboard/{user_id}/verdict-accuracy")
async def get_verdict_accuracy(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    items = await svc.get_verdict_accuracy(user_id=user_id)
    return {"items": items}


@router.get("/dashboard/verdict-accuracy")
async def get_verdict_accuracy_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    return await get_verdict_accuracy(user_id=_default_user_id(), session=session)


@router.get("/dashboard/{user_id}/thesis-performances")
async def get_thesis_performances(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    items = await svc.get_thesis_performances(user_id=user_id)
    return {"items": items}


@router.get("/dashboard/thesis-performances")
async def get_thesis_performances_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    return await get_thesis_performances(user_id=_default_user_id(), session=session)


@router.get("/dashboard/{user_id}/thesis/{thesis_id}/price-snapshots")
async def get_price_snapshots(
    user_id: str,
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    result = await svc.get_price_snapshots(user_id=user_id, thesis_id=thesis_id)
    if result is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Thesis not found")
    return result


@router.get("/dashboard/thesis/{thesis_id}/price-snapshots")
async def get_price_snapshots_default_user(
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    return await get_price_snapshots(
        user_id=_default_user_id(), thesis_id=thesis_id, session=session
    )


# ---------------------------------------------------------------------------
# 14. Portfolio — /dashboard/portfolio/trades  (position-centric, PnlService)
#               + /dashboard/portfolio         (thesis-centric, PortfolioQueryService)
# ---------------------------------------------------------------------------

from src.portfolio.pnl_service import PnlService  # noqa: E402


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
                # thesis_status: enables portfolio-renderer.js to render a warning badge
                # on rows where the linked thesis has been invalidated or closed.
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


@router.get("/dashboard/portfolio/trades")
async def get_portfolio_trades_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    return await get_portfolio_trades(
        user_id=_default_user_id(),
        session=session,
    )


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


@router.get("/dashboard/portfolio")
async def get_portfolio_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    return await get_portfolio(user_id=_default_user_id(), session=session)


# ---------------------------------------------------------------------------
# 15. Attention Panel — "Việc cần làm hôm nay"
# ---------------------------------------------------------------------------

@router.get("/dashboard/{user_id}/attention")
async def get_attention_needed(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    svc = DashboardService(session=session)
    return await svc.get_attention_needed(user_id=user_id, limit=limit)


@router.get("/dashboard/attention")
async def get_attention_needed_default_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    return await get_attention_needed(
        user_id=_default_user_id(), session=session, limit=limit
    )
