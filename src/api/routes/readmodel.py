"""Read-model API routes.

Owner: api segment — thin adapter only.
Delegates 100% to readmodel services + price enrichment from market segment.
No heavy business logic here.

Single-user mode:
- If owner_user_id is configured, the alias endpoints without /{user_id}
  will automatically use that user id.
- Multi-user endpoints remain intact for backward compatibility.

Route ordering rule (FastAPI matches in declaration order):
  Static/literal path segments MUST be declared before parameterised ones.
  e.g. /dashboard/theses/aggregate must come before /dashboard/theses/{thesis_id}
  otherwise FastAPI casts "aggregate" -> int and returns 422.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.bootstrap import get_quote_service
from src.platform.config import settings
from src.platform.db import AsyncSessionLocal
from src.api.deps import get_db
from src.readmodel.dashboard_service import DashboardService
from src.readmodel.leaderboard_service import LeaderboardService
from src.readmodel.schemas import (
    AttentionPanelResponse,
    ConvictionTimelineResponse,
    LeaderboardResponse,
    ReviewTimelineResponse,
    ThesisTimelineResponse,
)
from src.readmodel.timeline_service import ThesisTimelineService
from src.readmodel.today_loop_query_service import TodayLoopQueryService
from src.portfolio.pnl_service import PnlService
from src.portfolio.eod_snapshot_service import EodSnapshotService
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

    # Use a dedicated session for ScanService to prevent ISCE.
    # DashboardService.get_scan_latest() above leaves the shared session in a
    # "provisioning" state; running ScanService queries (has_recent_signal et al.)
    # on the same session triggers InvalidRequestError: concurrent operations not
    # permitted. A separate AsyncSession isolates the scan write path entirely.
    async with AsyncSessionLocal() as scan_session:
        scan_svc = ScanService(
            session=scan_session,
            quote_service=get_quote_service(),
        )
        await scan_svc.scan_user_if_stale(user_id=user_id, max_age_minutes=30)
        await scan_session.commit()

    # Re-read the freshly written snapshot through the request session.
    return await svc.get_scan_latest(user_id)


async def _build_price_map(tickers: list[str]) -> dict[str, float]:
    """Fetch current prices cho danh sach tickers tu QuoteService.

    Trả {} khi market đóng (MarketClosedError) hoặc fetch thất bại.
    Caller fallback sang avg_cost khi price_map thiếu key.
    """
    if not tickers:
        return {}
    try:
        quote_svc = get_quote_service()
        quotes = await quote_svc.get_bulk_quotes(tickers)
        return {q.ticker: q.price for q in quotes if q.price}
    except Exception as exc:
        if type(exc).__name__ == "MarketClosedError":
            # Ngoài giờ: im lặng, caller sẽ fallback sang avg_cost
            pass
        else:
            from src.platform.logging import get_logger as _gl
            _gl(__name__).warning("readmodel.price_map.fetch_failed error=%s", str(exc))
        return {}


async def _build_position_map(
    session: AsyncSession, user_id: str
) -> dict[str, tuple[float, float]]:
    """Load open positions for user -> {ticker: (qty, avg_cost)}."""
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


async def _fetch_price_and_position(
    session: AsyncSession,
    user_id: str,
    tickers: list[str],
) -> tuple[dict[str, float], dict[str, tuple[float, float]]]:
    """Sequential fetch: price_map then position_map.

    AsyncSession does not support concurrent operations — running
    _build_price_map (QuoteService/network) and _build_position_map
    (DB session) concurrently via asyncio.gather caused ISCE when the
    session was already provisioning a connection from the caller's
    prior query (get_theses_list, get_thesis_aggregate).

    Both awaits are run sequentially. Latency impact is negligible:
    _build_price_map is a network I/O call, not a second DB query.
    """
    price_map = await _build_price_map(tickers)
    position_map = await _build_position_map(session, user_id)
    return price_map, position_map


async def _resolve_thesis_ticker(session: AsyncSession, thesis_id: int) -> str | None:
    """Resolve ticker for a thesis_id. Returns None if thesis not found."""
    from src.thesis.models import Thesis

    result = await session.execute(select(Thesis.ticker).where(Thesis.id == thesis_id))
    row = result.scalar_one_or_none()
    return row


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


# ---------------------------------------------------------------------------
# 3. Thesis portfolio aggregate
# IMPORTANT: must be declared BEFORE /theses/{thesis_id}
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
# 4. Thesis detail — AFTER /theses/aggregate
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
# 5. Upcoming catalysts
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
# 8. Triggered alerts
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/alerts/triggered")
async def get_triggered_alerts(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200, description="Số alert tối đa trả về")] = 50,
) -> dict[str, Any]:
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
# 9. Recent signal events
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
# 13. Portfolio — Trades view
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/portfolio/trades")
async def get_portfolio_trades(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Portfolio trades — snapshot primary source + realtime overlay.

    Priority:
      1. Load latest EOD snapshot per ticker from position_daily_snapshots.
      2. If market is open: enrich current_price realtime via QuoteService.
      3. If no snapshot exists yet (first day): fallback to PnlService on-the-fly.
    """
    quote_svc = get_quote_service()
    eod_svc = EodSnapshotService(session=session, quote_service=quote_svc)
    snapshots = await eod_svc.get_latest_snapshots(user_id)

    # Fallback to on-the-fly PnlService if no snapshot written yet
    if not snapshots:
        pnl = await PnlService(session=session, quote_service=quote_svc).get_portfolio_pnl(user_id)
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
                    "thesis_id": p.thesis_id,
                    "price_stale": p.price_stale,
                }
                for p in pnl.positions
            ],
            "total_unrealized_pnl": pnl.total_unrealized_pnl,
            "total_unrealized_pct": pnl.total_unrealized_pct,
            "total_cost_basis": pnl.total_cost_basis,
            "total_market_value": pnl.total_market_value,
            "errors": pnl.errors,
            "source": "pnl_live",
        }

    # Build price map: start from snapshot close_price, overlay realtime if market open
    market_open = quote_svc.is_market_open()
    price_map: dict[str, float] = {}
    price_stale_map: dict[str, bool] = {}

    if market_open:
        tickers = [s.ticker for s in snapshots]
        for ticker in tickers:
            try:
                quote = await quote_svc.get_quote(ticker)
                price_map[ticker] = quote.price  # type: ignore[union-attr]
                price_stale_map[ticker] = False
            except Exception:
                # Fallback to snapshot close_price on individual ticker error
                pass

    positions_out = []
    total_cost = 0.0
    total_mkt = 0.0
    for snap in snapshots:
        current_price = price_map.get(snap.ticker, snap.close_price)
        stale = price_stale_map.get(snap.ticker, True)  # stale unless overridden by realtime
        cost_basis = snap.avg_cost * snap.qty
        market_value = current_price * snap.qty
        unrealized_pnl = (current_price - snap.avg_cost) * snap.qty
        unrealized_pct = (unrealized_pnl / cost_basis * 100) if cost_basis else 0.0
        total_cost += cost_basis
        total_mkt += market_value
        positions_out.append({
            "ticker": snap.ticker,
            "qty": snap.qty,
            "avg_cost": snap.avg_cost,
            "current_price": current_price,
            "cost_basis": cost_basis,
            "market_value": market_value,
            "unrealized_pnl": round(unrealized_pnl, 2),
            "unrealized_pct": round(unrealized_pct, 4),
            "thesis_id": snap.thesis_id,
            "price_stale": stale,
            "snapshot_date": str(snap.snapshot_date),
        })

    total_pnl = total_mkt - total_cost
    total_pct = (total_pnl / total_cost * 100) if total_cost else 0.0
    return {
        "positions": positions_out,
        "total_unrealized_pnl": round(total_pnl, 2),
        "total_unrealized_pct": round(total_pct, 4),
        "total_cost_basis": total_cost,
        "total_market_value": total_mkt,
        "errors": {},
        "source": "eod_snapshot" + ("+realtime" if market_open and price_map else ""),
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
# 14. Portfolio — Thesis view
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
# 15. Attention Panel — "Việc cần làm hôm nay" (Wave B)
# IMPORTANT: declared BEFORE /dashboard/{user_id}/... routes to avoid
# FastAPI casting "attention" as a path param in any future nested route.
# ---------------------------------------------------------------------------


@router.get("/dashboard/{user_id}/attention", response_model=AttentionPanelResponse)
async def get_attention_needed(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    enrich_prices: Annotated[
        bool,
        Query(
            description=(
                "Fetch live prices để kiểm tra stop_loss proximity. "
                "Tắt nếu muốn bỏ source stop_loss_proximity."
            )
        ),
    ] = True,
    limit: Annotated[
        int,
        Query(ge=1, le=50, description="Số attention items tối đa trả về"),
    ] = 20,
) -> AttentionPanelResponse:
    price_map: dict[str, float] = {}

    if enrich_prices:
        svc_pre = DashboardService(session)
        active_theses = await svc_pre.get_theses_list(user_id, status="active", limit=500)
        tickers = list({t["ticker"] for t in active_theses if t.get("ticker")})
        price_map = await _build_price_map(tickers)

    svc = DashboardService(session)
    return await svc.get_attention_needed(user_id, price_map=price_map, limit=limit)


@router.get("/dashboard/attention", response_model=AttentionPanelResponse)
async def get_attention_needed_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    enrich_prices: Annotated[bool, Query(description="Fetch live prices cho stop_loss proximity check")] = True,
    limit: Annotated[int, Query(ge=1, le=50, description="Số attention items tối đa")] = 20,
) -> AttentionPanelResponse:
    return await get_attention_needed(
        user_id=_default_user_id(),
        session=session,
        enrich_prices=enrich_prices,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# 16. Intelligence snapshot — Gap 4
#
# Route ordering: /dashboard/intelligence (static) BEFORE
# /dashboard/{user_id}/intelligence (parameterised).
#
# Returns:
#   200 OK  + intelligence dict  — snapshot available
#   204 No Content               — store not yet populated / engine hasn't run
# ---------------------------------------------------------------------------


@router.get("/dashboard/intelligence")
async def get_intelligence_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
) -> dict[str, Any] | None:
    svc = DashboardService(session)
    result = await svc.get_intelligence(_default_user_id())
    if result is None:
        response.status_code = 204
        return None
    return result


@router.get("/dashboard/{user_id}/intelligence")
async def get_intelligence(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
) -> dict[str, Any] | None:
    svc = DashboardService(session)
    result = await svc.get_intelligence(user_id)
    if result is None:
        response.status_code = 204
        return None
    return result


# ---------------------------------------------------------------------------
# 17. Today Loop — aggregated actionable signals (Gap 5)
#
# Route ordering: /dashboard/today-loop (static) BEFORE
# /dashboard/{user_id}/today-loop (parameterised).
#
# Sources aggregated by TodayLoopQueryService:
#   1. IntelligenceSnapshotStore  — priority_actions + risk_flags (in-process)
#   2. WatchlistAlert DB          — triggered today, snooze-filtered
#   3. SchedulerMonitor           — engine health for 4 tasks
#
# No AI calls. Graceful degradation per source.
# TodayLoopResult is a dataclass — serialized via asdict().
# ---------------------------------------------------------------------------


@router.get("/dashboard/today-loop")
async def get_today_loop_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Today loop cho owner_user_id — aggregated signals + engine health."""
    svc = TodayLoopQueryService(session)
    result = await svc.get_today_loop(_default_user_id())
    return asdict(result)


@router.get("/dashboard/{user_id}/today-loop")
async def get_today_loop(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Today loop cho user_id cụ thể — aggregated signals + engine health."""
    svc = TodayLoopQueryService(session)
    result = await svc.get_today_loop(user_id)
    return asdict(result)


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


@router.get("/thesis/{thesis_id}/timeline", response_model=ThesisTimelineResponse, response_model_by_alias=True)
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
# Review Timeline — 5 AI reviews gần nhất của một thesis
# ---------------------------------------------------------------------------


@router.get("/thesis/{thesis_id}/review-timeline", response_model=ReviewTimelineResponse)
async def get_review_timeline(
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[
        int,
        Query(ge=1, le=20, description="Số AI reviews gần nhất trả về (mới nhất trước)"),
    ] = 5,
) -> ReviewTimelineResponse:
    """Focused review timeline — N AI reviews gần nhất của một thesis."""
    svc = ThesisTimelineService(session)
    result = await svc.get_review_timeline(thesis_id, limit=limit)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Thesis {thesis_id} not found")
    return result


# ---------------------------------------------------------------------------
# Conviction Score Timeline — with live price injection (Option C fix)
# ---------------------------------------------------------------------------


@router.get("/thesis/{thesis_id}/conviction-timeline", response_model=ConvictionTimelineResponse)
async def get_conviction_timeline(
    thesis_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100, description="Số data-point tối đa trả về")] = 20,
    enrich_price: Annotated[
        bool,
        Query(
            description=(
                "Fetch live price từ QuoteService để inject vào điểm cuối cùng "
                "(Option C — fallback khi AI review chạy trước market snapshot job). "
                "Tắt nếu muốn dùng dữ liệu snapshot thuần túy."
            )
        ),
    ] = True,
) -> ConvictionTimelineResponse:
    """Conviction score timeline cho một thesis."""
    current_price: float | None = None

    if enrich_price:
        ticker = await _resolve_thesis_ticker(session, thesis_id)
        if ticker:
            price_map = await _build_price_map([ticker])
            current_price = price_map.get(ticker)

    svc = ThesisTimelineService(session)
    result = await svc.get_conviction_timeline(
        thesis_id,
        limit=limit,
        current_price=current_price,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"Thesis {thesis_id} not found")
    return result
