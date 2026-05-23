"""
SystemSnapshot builder — collects cross-segment state in parallel.
Owner: core segment.
Reads from segments via DB queries. No AI calls. Each fetch is isolated.
"""
from __future__ import annotations

import asyncio
import datetime

from src.core.schemas import (
    MarketContext, PortfolioContext, SystemSnapshot,
    ThesisContext, WatchlistContext,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)

_MARKET_OPEN_UTC  = datetime.time(2, 0)   # 09:00 ICT
_MARKET_CLOSE_UTC = datetime.time(8, 0)   # 15:00 ICT


def _current_market_phase() -> str:
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    t = now.time().replace(tzinfo=None)
    if now.weekday() >= 5:
        return "closed"
    if t < datetime.time(1, 30):
        return "pre_market"    # before 08:30 ICT
    if t < _MARKET_OPEN_UTC:
        return "pre_market"
    if t < datetime.time(4, 0):
        return "open"          # 09:00–11:00 ICT
    if t < datetime.time(6, 0):
        return "midday"        # 11:00–13:00 ICT
    if t < _MARKET_CLOSE_UTC:
        return "close"         # 13:00–15:00 ICT
    return "post_market"


async def _fetch_watchlist_context(user_id: str) -> WatchlistContext:
    try:
        from src.watchlist.scan_service import ScanService
        from src.platform.bootstrap import get_quote_service
        from src.thesis.ticker_direction_query import TickerDirectionQuery
        from src.platform.db import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            svc = ScanService(
                session=session,
                quote_service=get_quote_service(),
                ticker_direction_query=TickerDirectionQuery(session),
            )
            result = await svc.scan_user(user_id)

        triggered = [s for s in result.signals if getattr(s, "triggered", False)]
        volume_spikes = [
            s for s in result.signals
            if "VOLUME" in getattr(s, "signal_type", "").upper()
        ]
        top_tickers = list(
            {s.ticker for s in triggered} | {s.ticker for s in volume_spikes}
        )[:5]

        return WatchlistContext(
            triggered_alert_count=len(triggered),
            top_tickers=top_tickers,
            has_volume_spike=bool(volume_spikes),
        )
    except Exception as exc:
        logger.warning("snapshot.watchlist_fetch_failed", error=str(exc))
        return WatchlistContext()


async def _fetch_thesis_context(user_id: str) -> ThesisContext:
    try:
        from src.readmodel.thesis_query_service import ThesisQueryService
        from src.platform.db import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            qsvc = ThesisQueryService(session)
            stale = await qsvc.get_stale_theses(user_id, stale_days=3)

        return ThesisContext(stale_count=len(stale))
    except Exception as exc:
        logger.warning("snapshot.thesis_fetch_failed", error=str(exc))
        return ThesisContext()


async def _fetch_portfolio_context(user_id: str) -> PortfolioContext:
    try:
        from src.portfolio.service import PortfolioService
        from src.platform.bootstrap import get_quote_service
        from src.platform.db import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            svc = PortfolioService(
                session=session,
                quote_service=get_quote_service(),
            )
            summary = await svc.get_summary(user_id)

        return PortfolioContext(
            total_positions=summary.total_positions,
            unrealized_pnl_pct=summary.unrealized_pnl_pct,
        )
    except Exception as exc:
        logger.warning("snapshot.portfolio_fetch_failed", error=str(exc))
        return PortfolioContext()


async def build_snapshot(user_id: str, trigger_source: str = "") -> SystemSnapshot:
    """Parallel-fetch all segment contexts. One segment failing does not block others."""
    watchlist_ctx, thesis_ctx, portfolio_ctx = await asyncio.gather(
        _fetch_watchlist_context(user_id),
        _fetch_thesis_context(user_id),
        _fetch_portfolio_context(user_id),
    )
    market_ctx = MarketContext(market_phase=_current_market_phase())

    snapshot = SystemSnapshot(
        watchlist=watchlist_ctx,
        thesis=thesis_ctx,
        market=market_ctx,
        portfolio=portfolio_ctx,
        trigger_source=trigger_source,
    )
    logger.info(
        "snapshot.built",
        trigger_source=trigger_source,
        watchlist_alerts=watchlist_ctx.triggered_alert_count,
        stale_thesis=thesis_ctx.stale_count,
        phase=market_ctx.market_phase,
    )
    return snapshot
