"""Read-model API routes.

Owner: api segment — thin adapter only.
Delegates 100% to readmodel services (+ readmodel.enrichment cho price/position
inputs). No business logic here.

Single-user mode (Wave A):
- Moi endpoint co 2 URL: ``/dashboard/{user_id}/...`` (multi-user) va
  ``/dashboard/...`` (alias, dung settings.owner_user_id).
- Ca 2 URL tro ve CUNG 1 handler bang decorator xep chong; ``user_id`` duoc
  resolve boi dependency ``resolve_user_id`` (src/api/deps.py) — doc path
  param neu co, fallback owner_user_id, 500 neu chua cau hinh.
- OpenAPI: route multi-user khong liet ke ``user_id`` la path param (dependency
  doc tu request.path_params). Behaviour runtime khong doi.

Route ordering rule (FastAPI matches in declaration order):
  Static/literal path segments MUST be declared before parameterised ones.
  e.g. /dashboard/theses/aggregate must come before /dashboard/theses/{thesis_id}
  otherwise FastAPI casts "aggregate" -> int and returns 422.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import DbSession, UserId
from src.platform.bootstrap import get_quote_service
from src.platform.db import AsyncSessionLocal
from src.portfolio.eod_snapshot_service import EodSnapshotService
from src.portfolio.repository import PortfolioRepository
from src.readmodel.dashboard_service import DashboardService
from src.readmodel.enrichment import (
    build_price_map,
    fetch_price_and_position,
    list_thesis_tickers,
    resolve_thesis_ticker,
)
from src.readmodel.intelligence_read_service import IntelligenceReadService
from src.readmodel.leaderboard_service import LeaderboardService
from src.readmodel.portfolio_query_service import build_trades_payload
from src.readmodel.schemas import (
    AttentionPanelResponse,
    ConvictionTimelineResponse,
    LeaderboardResponse,
    ReviewTimelineResponse,
    ThesisTimelineResponse,
)
from src.readmodel.timeline_service import ThesisTimelineService
from src.readmodel.today_loop_query_service import TodayLoopQueryService
from src.watchlist.scan_service import ScanService

router = APIRouter(prefix="/readmodel", tags=["readmodel"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _paginated(items: list) -> dict[str, Any]:
    """Wrap list thanh shape nhat quan: {items, total}."""
    return {"items": items, "total": len(items)}


def _not_found(thesis_id: int) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Thesis {thesis_id} not found")


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


# ---------------------------------------------------------------------------
# 1. Stats — KPI tong quan
# ---------------------------------------------------------------------------


@router.get("/dashboard/stats")
@router.get("/dashboard/{user_id}/stats")
async def get_stats(user_id: UserId, session: DbSession) -> dict[str, Any]:
    return await DashboardService(session).get_stats(user_id)


# ---------------------------------------------------------------------------
# 2. Theses list — enriched with live price + avg_cost from positions
# ---------------------------------------------------------------------------


@router.get("/dashboard/theses")
@router.get("/dashboard/{user_id}/theses")
async def get_theses_list(
    user_id: UserId,
    session: DbSession,
    status: Annotated[str, Query()] = "active",
    ticker: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    enrich_prices: Annotated[
        bool,
        Query(description="Fetch giá hiện tại + avg_cost từ positions để tính P&L"),
    ] = True,
) -> dict[str, Any]:
    price_map: dict[str, float] = {}
    position_map: dict[str, tuple[float, float]] = {}

    if enrich_prices:
        # Wave A: query nhe lay ticker thay cho full get_theses_list lan 1.
        tickers = await list_thesis_tickers(session, user_id, status=status, ticker=ticker, limit=limit)
        price_map, position_map = await fetch_price_and_position(session, user_id, tickers)

    items = await DashboardService(session).get_theses_list(
        user_id,
        status=status,
        ticker=ticker,
        limit=limit,
        price_map=price_map,
        position_map=position_map,
    )
    return _paginated(items)


# ---------------------------------------------------------------------------
# 3. Thesis portfolio aggregate
# IMPORTANT: must be declared BEFORE /theses/{thesis_id}
# ---------------------------------------------------------------------------


@router.get("/dashboard/theses/aggregate")
@router.get("/dashboard/{user_id}/theses/aggregate")
async def get_thesis_aggregate(
    user_id: UserId,
    session: DbSession,
    enrich_prices: Annotated[
        bool,
        Query(description="Fetch live price + position map để tính P&L aggregate"),
    ] = True,
) -> dict[str, Any]:
    price_map: dict[str, float] = {}
    position_map: dict[str, tuple[float, float]] = {}

    if enrich_prices:
        tickers = await list_thesis_tickers(session, user_id, status="active")
        price_map, position_map = await fetch_price_and_position(session, user_id, tickers)

    return await DashboardService(session).get_thesis_portfolio_aggregate(
        user_id,
        price_map=price_map,
        position_map=position_map,
    )


# ---------------------------------------------------------------------------
# 4. Thesis detail — AFTER /theses/aggregate
# ---------------------------------------------------------------------------


@router.get("/dashboard/theses/{thesis_id}")
@router.get("/dashboard/{user_id}/theses/{thesis_id}")
async def get_thesis_detail(user_id: UserId, thesis_id: int, session: DbSession) -> dict[str, Any]:
    result = await DashboardService(session).get_thesis_detail(user_id, thesis_id)
    if result is None:
        raise _not_found(thesis_id)
    return result


# ---------------------------------------------------------------------------
# 5. Upcoming catalysts
# ---------------------------------------------------------------------------


@router.get("/dashboard/catalysts/upcoming")
@router.get("/dashboard/{user_id}/catalysts/upcoming")
async def get_upcoming_catalysts(
    user_id: UserId,
    session: DbSession,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> dict[str, Any]:
    return _paginated(await DashboardService(session).get_upcoming_catalysts(user_id, days=days))


# ---------------------------------------------------------------------------
# 6. Latest scan snapshot
# ---------------------------------------------------------------------------


@router.get("/dashboard/scan/latest")
@router.get("/dashboard/{user_id}/scan/latest")
async def get_scan_latest(user_id: UserId, session: DbSession) -> dict[str, Any] | None:
    return await _ensure_scan_snapshot(session, user_id)


# ---------------------------------------------------------------------------
# 7. Recommendations — Intelligence Engine primary actions
# ---------------------------------------------------------------------------

_EMPTY_RECOMMENDATIONS: dict[str, Any] = {
    "is_fresh": False,
    "overall_verdict": None,
    "confidence": None,
    "market_context": None,
    "priority_actions": [],
    "risk_flags": [],
    "watch_list": [],
    "generated_at": None,
    "is_stale": True,
}


@router.get("/dashboard/recommendations")
@router.get("/dashboard/{user_id}/recommendations")
async def get_recommendations(user_id: UserId, session: DbSession) -> dict[str, Any]:
    """Return latest Intelligence Engine verdict + priority actions for dashboard.

    Data source: IntelligenceSnapshotStore (in-process) — no AI call.
    Populated by IntelligenceSnapshotSubscriber after each engine cycle.
    Returns empty shell when engine hasn't run yet (is_fresh=False).
    """
    data = await IntelligenceReadService(session).get_intelligence(user_id)
    if data is None:
        return dict(_EMPTY_RECOMMENDATIONS)
    return {"is_fresh": True, **data}


# ---------------------------------------------------------------------------
# 8. Brief snapshots + feedback
# ---------------------------------------------------------------------------


@router.get("/dashboard/brief/latest")
@router.get("/dashboard/{user_id}/brief/latest")
async def get_brief_latest(
    user_id: UserId,
    session: DbSession,
    phase: Annotated[Literal["morning", "eod"], Query()] = "morning",
) -> dict[str, Any] | None:
    return await DashboardService(session).get_brief_latest(user_id, phase=phase)


@router.get("/dashboard/brief/feedback-summary")
@router.get("/dashboard/{user_id}/brief/feedback-summary")
async def get_brief_feedback_summary(
    user_id: UserId,
    session: DbSession,
    days: Annotated[int, Query(ge=1, le=90, description="Window tính acted_rate (ngày)")] = 30,
) -> dict[str, Any]:
    return await DashboardService(session).get_brief_feedback_summary(user_id, days=days)


# ---------------------------------------------------------------------------
# 9. Triggered alerts
# ---------------------------------------------------------------------------


@router.get("/dashboard/alerts/triggered")
@router.get("/dashboard/{user_id}/alerts/triggered")
async def get_triggered_alerts(
    user_id: UserId,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=200, description="Số alert tối đa trả về")] = 50,
) -> dict[str, Any]:
    return _paginated(await DashboardService(session).get_triggered_alerts(user_id, limit=limit))


# ---------------------------------------------------------------------------
# 10. Recent signal events
# ---------------------------------------------------------------------------


@router.get("/dashboard/signals/recent")
@router.get("/dashboard/{user_id}/signals/recent")
async def get_recent_signals(
    user_id: UserId,
    session: DbSession,
    ticker: Annotated[
        str | None,
        Query(description="Filter theo mã cụ thể (VD: VCB). Bỏ qua để lấy toàn bộ watchlist."),
    ] = None,
    days: Annotated[int, Query(ge=1, le=90, description="Window thời gian (ngày)")] = 7,
    limit: Annotated[int, Query(ge=1, le=200, description="Số signal tối đa trả về")] = 50,
    stale_days: Annotated[
        int,
        Query(ge=0, le=30, description="Bỏ qua ticker không có tín hiệu mới trong N ngày. 0 = tắt bộ lọc."),
    ] = 3,
) -> dict[str, Any]:
    svc = DashboardService(session)
    return _paginated(
        await svc.get_recent_signals(user_id, ticker=ticker, days=days, limit=limit, stale_days=stale_days)
    )


# ---------------------------------------------------------------------------
# 11. Backtesting — verdict accuracy / thesis performances / price snapshots
# ---------------------------------------------------------------------------


@router.get("/dashboard/backtesting/verdict-accuracy")
@router.get("/dashboard/{user_id}/backtesting/verdict-accuracy")
async def get_verdict_accuracy(user_id: UserId, session: DbSession) -> dict[str, Any]:
    return _paginated(await DashboardService(session).get_verdict_accuracy(user_id))


@router.get("/dashboard/backtesting/thesis-performances")
@router.get("/dashboard/{user_id}/backtesting/thesis-performances")
async def get_thesis_performances(
    user_id: UserId,
    session: DbSession,
    ticker: Annotated[str | None, Query(description="Filter theo ticker")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    return await DashboardService(session).get_thesis_performances(user_id, ticker=ticker, limit=limit)


@router.get("/dashboard/backtesting/price-snapshots/{thesis_id}")
@router.get("/dashboard/{user_id}/backtesting/price-snapshots/{thesis_id}")
async def get_price_snapshots(user_id: UserId, thesis_id: int, session: DbSession) -> dict[str, Any]:
    result = await DashboardService(session).get_price_snapshots(user_id, thesis_id)
    if result is None:
        raise _not_found(thesis_id)
    return result


# ---------------------------------------------------------------------------
# 12. Portfolio — Trades view
# ---------------------------------------------------------------------------


@router.get("/dashboard/portfolio/trades")
@router.get("/dashboard/{user_id}/portfolio/trades")
async def get_portfolio_trades(user_id: UserId, session: DbSession) -> dict[str, Any]:
    """Portfolio trades — live positions là source of truth cho qty/avg_cost.

    Priority:
      1. qty/avg_cost/thesis_id từ bảng positions (live, đã commit).
      2. Giá: QuoteService (market mở → realtime; đóng cửa → last_known cache),
         fallback close_price của snapshot gần nhất per ticker.
      3. Snapshot KHÔNG còn là nguồn qty/avg_cost — loại trừ stale khi snapshot
         chưa/miss ghi và loại vị thế đã đóng (snapshot cũ không bao giờ bị xoá).
    """
    quote_svc = get_quote_service()
    repo = PortfolioRepository(session)
    live_positions = [p for p in await repo.list_open_positions(user_id) if p.qty > 0]

    if not live_positions:
        return build_trades_payload([], {}, {}, market_open=False)

    # Snapshot gần nhất per ticker — CHỈ còn vai trò close_price fallback
    eod_svc = EodSnapshotService(session=session, quote_service=quote_svc)
    snapshots = await eod_svc.get_latest_snapshots(user_id)
    snap_close: dict[str, tuple[float, str]] = {s.ticker: (s.close_price, str(s.snapshot_date)) for s in snapshots}

    # 1 bulk quote call (dung chung build_price_map voi cac route khac);
    # ticker fail/thieu trong batch -> roi ve snapshot close trong builder.
    market_open = quote_svc.is_market_open()
    price_map = await build_price_map([pos.ticker for pos in live_positions])

    return build_trades_payload(live_positions, snap_close, price_map, market_open)


# ---------------------------------------------------------------------------
# 13. Portfolio — Thesis view
# ---------------------------------------------------------------------------


@router.get("/dashboard/portfolio")
@router.get("/dashboard/{user_id}/portfolio")
async def get_portfolio(
    user_id: UserId,
    session: DbSession,
    enrich_prices: Annotated[
        bool,
        Query(description="Fetch gia hien tai tu QuoteService de tinh P&L realtime"),
    ] = True,
) -> dict[str, Any]:
    price_map: dict[str, float] = {}
    if enrich_prices:
        tickers = await list_thesis_tickers(session, user_id, status="active")
        price_map = await build_price_map(tickers)

    return await DashboardService(session).get_portfolio(user_id, price_map=price_map)


# ---------------------------------------------------------------------------
# 14. Attention Panel — "Việc cần làm hôm nay"
# ---------------------------------------------------------------------------


@router.get("/dashboard/attention", response_model=AttentionPanelResponse)
@router.get("/dashboard/{user_id}/attention", response_model=AttentionPanelResponse)
async def get_attention_needed(
    user_id: UserId,
    session: DbSession,
    enrich_prices: Annotated[
        bool,
        Query(description="Fetch live prices để kiểm tra stop_loss proximity. Tắt nếu muốn bỏ source stop_loss_proximity."),
    ] = True,
    limit: Annotated[int, Query(ge=1, le=50, description="Số attention items tối đa trả về")] = 20,
) -> AttentionPanelResponse:
    price_map: dict[str, float] = {}
    if enrich_prices:
        tickers = await list_thesis_tickers(session, user_id, status="active")
        price_map = await build_price_map(tickers)

    return await DashboardService(session).get_attention_needed(user_id, price_map=price_map, limit=limit)


# ---------------------------------------------------------------------------
# 15. Intelligence snapshot
#   200 OK  + intelligence dict  — snapshot available
#   204 No Content               — store not yet populated / engine hasn't run
# ---------------------------------------------------------------------------


@router.get("/dashboard/intelligence")
@router.get("/dashboard/{user_id}/intelligence")
async def get_intelligence(user_id: UserId, session: DbSession, response: Response) -> dict[str, Any] | None:
    result = await DashboardService(session).get_intelligence(user_id)
    if result is None:
        response.status_code = 204
        return None
    return result


# ---------------------------------------------------------------------------
# 16. Today Loop — aggregated actionable signals
#
# Sources aggregated by TodayLoopQueryService:
#   1. IntelligenceSnapshotStore  — priority_actions + risk_flags (in-process)
#   2. WatchlistAlert DB          — triggered today, snooze-filtered
#   3. SchedulerMonitor           — engine health for 4 tasks
# No AI calls. Graceful degradation per source. TodayLoopResult is a dataclass.
# ---------------------------------------------------------------------------


@router.get("/dashboard/today-loop")
@router.get("/dashboard/{user_id}/today-loop")
async def get_today_loop(user_id: UserId, session: DbSession) -> dict[str, Any]:
    """Today loop — aggregated signals + engine health."""
    result = await TodayLoopQueryService(session).get_today_loop(user_id)
    return asdict(result)


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


@router.get("/leaderboard", response_model=LeaderboardResponse)
@router.get("/leaderboard/{user_id}", response_model=LeaderboardResponse)
async def get_leaderboard(
    user_id: UserId,
    session: DbSession,
    sort_by: Annotated[Literal["score", "pnl"], Query()] = "score",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> LeaderboardResponse:
    return await LeaderboardService(session).get_leaderboard(user_id, sort_by=sort_by, limit=limit)


# ---------------------------------------------------------------------------
# Thesis timeline — general event log
# ---------------------------------------------------------------------------


@router.get("/thesis/{thesis_id}/timeline", response_model=ThesisTimelineResponse, response_model_by_alias=True)
async def get_thesis_timeline(thesis_id: int, session: DbSession) -> ThesisTimelineResponse:
    result = await ThesisTimelineService(session).get_timeline(thesis_id)
    if result is None:
        raise _not_found(thesis_id)
    return result


# ---------------------------------------------------------------------------
# Review Timeline — N AI reviews gần nhất của một thesis
# ---------------------------------------------------------------------------


@router.get("/thesis/{thesis_id}/review-timeline", response_model=ReviewTimelineResponse)
async def get_review_timeline(
    thesis_id: int,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=20, description="Số AI reviews gần nhất trả về (mới nhất trước)")] = 5,
) -> ReviewTimelineResponse:
    result = await ThesisTimelineService(session).get_review_timeline(thesis_id, limit=limit)
    if result is None:
        raise _not_found(thesis_id)
    return result


# ---------------------------------------------------------------------------
# Conviction Score Timeline — with live price injection (Option C fix)
# ---------------------------------------------------------------------------


@router.get("/thesis/{thesis_id}/conviction-timeline", response_model=ConvictionTimelineResponse)
async def get_conviction_timeline(
    thesis_id: int,
    session: DbSession,
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
        ticker = await resolve_thesis_ticker(session, thesis_id)
        if ticker:
            price_map = await build_price_map([ticker])
            current_price = price_map.get(ticker)

    result = await ThesisTimelineService(session).get_conviction_timeline(
        thesis_id,
        limit=limit,
        current_price=current_price,
    )
    if result is None:
        raise _not_found(thesis_id)
    return result
