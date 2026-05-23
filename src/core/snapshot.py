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
    """Fetch thesis state: stale + drift + invalidated counts.

    Tries each ThesisQueryService method independently so a missing method
    degrades gracefully to 0 rather than failing the whole context.
    """
    try:
        from src.readmodel.thesis_query_service import ThesisQueryService
        from src.platform.db import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            qsvc = ThesisQueryService(session)

            # stale (original)
            stale = await qsvc.get_stale_theses(user_id, stale_days=3)
            stale_count = len(stale)

            # drift — graceful fallback if method not yet implemented
            drift_count = 0
            if hasattr(qsvc, "get_drift_theses"):
                try:
                    drift = await qsvc.get_drift_theses(user_id)
                    drift_count = len(drift)
                except Exception as drift_exc:
                    logger.warning("snapshot.thesis_drift_fetch_failed", error=str(drift_exc))

            # invalidated — graceful fallback if method not yet implemented
            invalidated_count = 0
            if hasattr(qsvc, "get_invalidated_theses"):
                try:
                    invalidated = await qsvc.get_invalidated_theses(user_id)
                    invalidated_count = len(invalidated)
                except Exception as inv_exc:
                    logger.warning("snapshot.thesis_invalidated_fetch_failed", error=str(inv_exc))

        return ThesisContext(
            stale_count=stale_count,
            drift_detected_count=drift_count,
            invalidated_count=invalidated_count,
        )
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


async def build_snapshot(
    user_id: str,
    trigger_source: str = "",
    signal_engine_summary: str = "",
) -> SystemSnapshot:
    """Parallel-fetch all segment contexts. One segment failing does not block others.

    Args:
        user_id:               Owner of this snapshot.
        trigger_source:        What triggered this engine cycle.
        signal_engine_summary: Optional narrative from a prior SignalEngineCompletedEvent.
                               Passed through to SystemSnapshot.signal_engine_summary
                               and injected into the AI verdict prompt for richer context.
    """
    watchlist_ctx, thesis_ctx, portfolio_ctx = await asyncio.gather(
        _fetch_watchlist_context(user_id),
        _fetch_thesis_context(user_id),
        _fetch_portfolio_context(user_id),
    )
    market_ctx = MarketContext(market_phase=_current_market_phase())

    snap = SystemSnapshot(
        watchlist=watchlist_ctx,
        thesis=thesis_ctx,
        market=market_ctx,
        portfolio=portfolio_ctx,
        signal_engine_summary=signal_engine_summary,
        trigger_source=trigger_source,
    )
    logger.info(
        "snapshot.built",
        trigger_source=trigger_source,
        watchlist_alerts=watchlist_ctx.triggered_alert_count,
        stale_thesis=thesis_ctx.stale_count,
        drift_thesis=thesis_ctx.drift_detected_count,
        invalidated_thesis=thesis_ctx.invalidated_count,
        phase=market_ctx.market_phase,
        has_signal_engine_summary=bool(signal_engine_summary),
    )
    return snap
