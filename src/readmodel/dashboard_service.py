"""DashboardService — facade cho readmodel segment.

Owner: readmodel segment.

Endpoints served (via src/api/routes/readmodel.py):
    get_stats()                         — delegates to StatsService
    get_theses_list()                   — delegates to ThesisQueryService
    get_thesis_detail()                 — delegates to ThesisQueryService
    get_upcoming_catalysts()            — delegates to ThesisQueryService
    get_thesis_portfolio_aggregate()    — delegates to ThesisQueryService
    get_scan_latest()                   — snapshot scan gan nhat (WatchlistScan)
    get_brief_latest()                  — snapshot brief gan nhat (BriefSnapshot)
    get_brief_feedback_summary()        — feedback summary cho brief (BriefFeedback)
    get_triggered_alerts()              — alerts da fire, chua xu ly (Alert)
    get_recent_signals()                — signal history per ticker (SignalEvent)
    get_verdict_accuracy()              — delegates to BacktestingService
    get_thesis_performances()           — delegates to BacktestingService
    get_price_snapshots()               — delegates to BacktestingService
    get_portfolio()                     — delegates to PortfolioQueryService

Design rules:
- This class is a thin facade — no query logic lives here.
- SELECT-only: no writes, no business logic, no AI calls.
- All public methods are async and accept an AsyncSession (via constructor).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.readmodel.backtesting_service import BacktestingService
from src.readmodel.portfolio_query_service import PortfolioQueryService
from src.readmodel.stats_service import StatsService
from src.readmodel.thesis_query_service import ThesisQueryService

logger = get_logger(__name__)


class QuoteBatchReader(Protocol):
    """Minimal batch-quote interface required by DashboardService.

    Any object with a compatible get_quotes() method satisfies this contract.
    Keeps DashboardService loosely coupled from the market segment.
    """

    async def get_quotes(self, tickers: list[str]): ...  # noqa: D102


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._stats = StatsService(session)
        self._thesis_query = ThesisQueryService(session)
        self._portfolio_query = PortfolioQueryService(session)
        self._backtesting = BacktestingService(session)

    # ------------------------------------------------------------------
    # 1. Stats — delegates to StatsService
    # ------------------------------------------------------------------

    async def get_stats(self, user_id: str) -> dict[str, Any]:
        return await self._stats.get_stats(user_id)

    # ------------------------------------------------------------------
    # 2-5. Thesis queries — delegates to ThesisQueryService
    # ------------------------------------------------------------------

    async def get_theses_list(
        self,
        user_id: str,
        status: str | None = "active",
        ticker: str | None = None,
        limit: int = 200,
        price_map: dict[str, float] | None = None,
        position_map: dict[str, tuple[float, float]] | None = None,
    ) -> list[dict[str, Any]]:
        return await self._thesis_query.get_theses_list(
            user_id=user_id,
            status=status,
            ticker=ticker,
            limit=limit,
            price_map=price_map,
            position_map=position_map,
        )

    async def get_thesis_detail(self, user_id: str, thesis_id: int) -> dict[str, Any] | None:
        return await self._thesis_query.get_thesis_detail(user_id, thesis_id)

    async def get_upcoming_catalysts(self, user_id: str, days: int = 30) -> list[dict[str, Any]]:
        return await self._thesis_query.get_upcoming_catalysts(user_id, days=days)

    async def get_thesis_portfolio_aggregate(
        self,
        user_id: str,
        price_map: dict[str, float] | None = None,
        position_map: dict[str, tuple[float, float]] | None = None,
    ) -> dict[str, Any]:
        """Thesis portfolio aggregate: counts + P&L totals + breakdowns.

        Delegates to ThesisQueryService.get_thesis_portfolio_aggregate().
        price_map / position_map optionally injected by the route layer.
        """
        return await self._thesis_query.get_thesis_portfolio_aggregate(
            user_id=user_id,
            price_map=price_map,
            position_map=position_map,
        )

    # ------------------------------------------------------------------
    # 6. Latest scan snapshot — cross-segment (watchlist)
    # ------------------------------------------------------------------

    async def get_scan_latest(self, user_id: str) -> dict[str, Any] | None:
        try:
            from src.watchlist.models import WatchlistScan
        except ImportError:
            logger.warning(
                "get_scan_latest.import_error", detail="WatchlistScan model not available"
            )
            return None

        try:
            row = (
                await self._session.execute(
                    select(WatchlistScan)
                    .where(WatchlistScan.user_id == user_id)
                    .order_by(WatchlistScan.scanned_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            if not row:
                return None

            # Parse summary neu la valid JSON — tuong tu get_brief_latest().
            # Neu khong parse duoc, fallback ve {"raw": summary} de giu backward compat.
            try:
                parsed_summary = json.loads(row.summary)
                if not isinstance(parsed_summary, dict):
                    parsed_summary = {"raw": row.summary}
            except (json.JSONDecodeError, TypeError):
                parsed_summary = {"raw": row.summary}

            return {
                **parsed_summary,
                "id": row.id,
                "user_id": row.user_id,
                "summary": row.summary,
                "scanned_at": row.scanned_at.isoformat() if row.scanned_at else None,
            }
        except Exception as exc:
            logger.warning("get_scan_latest.db_error", error=str(exc))
            return None

    # ------------------------------------------------------------------
    # 7. Latest brief snapshot — cross-segment (briefing)
    # ------------------------------------------------------------------

    async def get_brief_latest(self, user_id: str, phase: str = "morning") -> dict[str, Any] | None:
        try:
            from src.briefing.models import BriefSnapshot, BriefFeedback
        except ImportError:
            logger.warning(
                "get_brief_latest.import_error", detail="BriefSnapshot model not available"
            )
            return None

        try:
            row = (
                await self._session.execute(
                    select(BriefSnapshot)
                    .where(
                        BriefSnapshot.user_id == user_id,
                        BriefSnapshot.phase == phase,
                    )
                    .order_by(BriefSnapshot.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            if not row:
                return None

            try:
                parsed_content = json.loads(row.content)
            except (json.JSONDecodeError, TypeError):
                parsed_content = {"summary": row.content, "content": row.content}

            # Latest feedback outcome for this brief snapshot
            feedback_outcome = (
                await self._session.execute(
                    select(BriefFeedback.outcome)
                    .where(BriefFeedback.brief_snapshot_id == row.id)
                    .order_by(BriefFeedback.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            return {
                **parsed_content,
                "id": row.id,
                "user_id": row.user_id,
                "phase": row.phase,
                "content": row.content,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "feedback_outcome": feedback_outcome,
            }
        except Exception as exc:
            logger.warning("get_brief_latest.db_error", error=str(exc))
            return None

    async def get_brief_feedback_summary(
        self, user_id: str, days: int = 30
    ) -> dict[str, Any]:
        """Return brief feedback summary for user."""
        try:
            from src.briefing.models import BriefFeedback
        except ImportError:
            logger.warning(
                "get_brief_feedback_summary.import_error",
                detail="BriefFeedback model not available",
            )
            return {
                "last_feedback_outcome": None,
                "last_feedback_at": None,
                "acted_rate_30d": None,
                "total_feedbacks_30d": 0,
            }

        try:
            latest = (
                await self._session.execute(
                    select(BriefFeedback)
                    .where(BriefFeedback.user_id == user_id)
                    .order_by(BriefFeedback.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            since = datetime.now(UTC) - timedelta(days=days)
            rows_30d = (
                await self._session.execute(
                    select(BriefFeedback.outcome)
                    .where(
                        BriefFeedback.user_id == user_id,
                        BriefFeedback.created_at >= since,
                    )
                )
            ).scalars().all()

            total = len(rows_30d)
            acted_count = sum(1 for o in rows_30d if o == "acted")
            acted_rate = round(acted_count / total, 3) if total > 0 else None

            return {
                "last_feedback_outcome": latest.outcome if latest else None,
                "last_feedback_at": latest.created_at.isoformat() if latest else None,
                "acted_rate_30d": acted_rate,
                "total_feedbacks_30d": total,
            }
        except Exception as exc:
            logger.warning("get_brief_feedback_summary.db_error", error=str(exc))
            return {
                "last_feedback_outcome": None,
                "last_feedback_at": None,
                "acted_rate_30d": None,
                "total_feedbacks_30d": 0,
            }

    # ------------------------------------------------------------------
    # 8. Triggered alerts — cross-segment (watchlist)
    # ------------------------------------------------------------------

    async def get_triggered_alerts(
        self,
        user_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Tra list alerts da fire (status=TRIGGERED), sap xep theo triggered_at desc."""
        try:
            from src.watchlist.models import Alert, AlertStatus
        except ImportError:
            logger.warning(
                "get_triggered_alerts.import_error", detail="Alert model not available"
            )
            return []

        try:
            rows = (
                await self._session.execute(
                    select(Alert)
                    .where(
                        Alert.user_id == user_id,
                        Alert.status == AlertStatus.TRIGGERED,
                    )
                    .order_by(Alert.triggered_at.desc())
                    .limit(limit)
                )
            ).scalars().all()

            return [
                {
                    "id": r.id,
                    "ticker": r.ticker,
                    "condition_type": str(r.condition_type),
                    "threshold": r.threshold,
                    "triggered_price": r.triggered_price,
                    "triggered_at": r.triggered_at.isoformat() if r.triggered_at else None,
                    "priority": r.priority,
                    "label": r.label,
                    "auto_reactivate": r.auto_reactivate,
                    "note": r.note,
                    "watchlist_item_id": r.watchlist_item_id,
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("get_triggered_alerts.db_error", error=str(exc))
            return []

    # ------------------------------------------------------------------
    # 9. Recent signal events — grouped by ticker+signal_type
    # ------------------------------------------------------------------

    async def get_recent_signals(
        self,
        user_id: str,
        ticker: str | None = None,
        days: int = 7,
        limit: int = 50,
        group_by_ticker: bool = True,
    ) -> list[dict[str, Any]]:
        """Tra SignalEvent gan day cho user, grouped theo ticker.

        Khi group_by_ticker=True (default):
          - Group cac signal cung ticker+signal_type trong cung ngay thanh 1 row.
          - Moi ticker tra ve 1 entry voi fields:
              ticker, signal_types[], max_strength, max_confidence,
              count, first_seen, last_seen, source.
          - Loai bo noise: cac signal lap lai moi 5 phut se collapse thanh count.

        Khi group_by_ticker=False:
          - Tra raw list nhu cu (dung cho detail view / per-ticker drill-down).
        """
        try:
            from src.watchlist.models import SignalEvent
        except ImportError:
            logger.warning(
                "get_recent_signals.import_error", detail="SignalEvent model not available"
            )
            return []

        try:
            since = datetime.now(UTC) - timedelta(days=days)

            stmt = (
                select(SignalEvent)
                .where(
                    SignalEvent.user_id == user_id,
                    SignalEvent.occurred_at >= since,
                )
                .order_by(SignalEvent.occurred_at.desc())
                .limit(500)  # fetch nhieu hon de group phia Python
            )

            if ticker is not None:
                stmt = stmt.where(SignalEvent.ticker == ticker.upper())

            rows = (await self._session.execute(stmt)).scalars().all()

            if not group_by_ticker:
                # Raw mode — backward compat
                result = []
                for r in rows[:limit]:
                    try:
                        metadata = json.loads(r.metadata_json) if r.metadata_json else None
                    except (json.JSONDecodeError, TypeError):
                        metadata = None
                    result.append({
                        "id": r.id,
                        "event_id": r.event_id,
                        "ticker": r.ticker,
                        "signal_type": r.signal_type,
                        "strength": r.strength,
                        "confidence": r.confidence,
                        "source": r.source,
                        "metadata": metadata,
                        "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
                        "processed_at": r.processed_at.isoformat() if r.processed_at else None,
                    })
                return result

            # --- Grouped mode (default) ---
            # Group by ticker, collapse signal_types, keep max strength/confidence
            groups: dict[str, dict[str, Any]] = {}

            for r in rows:
                key = r.ticker
                if key not in groups:
                    groups[key] = {
                        "ticker": r.ticker,
                        "signal_types": set(),
                        "max_strength": 0.0,
                        "max_confidence": 0.0,
                        "count": 0,
                        "first_seen": r.occurred_at,
                        "last_seen": r.occurred_at,
                        "source": r.source,
                    }
                g = groups[key]
                g["signal_types"].add(r.signal_type)
                g["max_strength"] = max(g["max_strength"], r.strength or 0.0)
                g["max_confidence"] = max(g["max_confidence"], r.confidence or 0.0)
                g["count"] += 1
                if r.occurred_at and r.occurred_at < g["first_seen"]:
                    g["first_seen"] = r.occurred_at
                if r.occurred_at and r.occurred_at > g["last_seen"]:
                    g["last_seen"] = r.occurred_at

            # Sort by max_strength desc, then count desc
            sorted_groups = sorted(
                groups.values(),
                key=lambda g: (g["max_strength"], g["count"]),
                reverse=True,
            )

            return [
                {
                    "ticker": g["ticker"],
                    "signal_types": sorted(g["signal_types"]),
                    "max_strength": round(g["max_strength"], 3),
                    "max_confidence": round(g["max_confidence"], 3),
                    "count": g["count"],
                    "first_seen": g["first_seen"].isoformat() if g["first_seen"] else None,
                    "last_seen": g["last_seen"].isoformat() if g["last_seen"] else None,
                    "source": g["source"],
                }
                for g in sorted_groups[:limit]
            ]
        except Exception as exc:
            logger.warning("get_recent_signals.db_error", error=str(exc))
            return []

    # ------------------------------------------------------------------
    # 10-12. Backtesting — delegates to BacktestingService
    # ------------------------------------------------------------------

    async def get_verdict_accuracy(self, user_id: str) -> list[dict[str, Any]]:
        return await self._backtesting.get_verdict_accuracy(user_id)

    async def get_thesis_performances(
        self,
        user_id: str,
        ticker: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await self._backtesting.get_thesis_performances(user_id, ticker=ticker, limit=limit)

    async def get_price_snapshots(self, user_id: str, thesis_id: int) -> dict[str, Any] | None:
        return await self._backtesting.get_price_snapshots(user_id, thesis_id)

    # ------------------------------------------------------------------
    # 13. Portfolio — delegates to PortfolioQueryService
    # ------------------------------------------------------------------

    async def get_portfolio(
        self,
        user_id: str,
        price_map: dict[str, float] | None = None,
        quote_service: QuoteBatchReader | None = None,
    ) -> dict[str, Any]:
        """Return portfolio data for user, optionally enriched with live prices."""
        if price_map is None and quote_service is not None:
            theses = await self._thesis_query.get_theses_list(
                user_id=user_id, status="active", limit=500
            )
            tickers = list({t["ticker"] for t in theses if t.get("ticker")})
            if tickers:
                try:
                    quotes = await quote_service.get_quotes(tickers)
                    price_map = {q.ticker: q.close for q in quotes if q.close is not None}
                except Exception as exc:
                    logger.warning(
                        "dashboard_service.get_portfolio.price_fetch_failed",
                        error=str(exc),
                    )
                    price_map = {}

        return await self._portfolio_query.get_portfolio(user_id, price_map=price_map)
