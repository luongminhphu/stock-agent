"""DashboardService — facade cho readmodel segment.

Owner: readmodel segment.

Endpoints served (via src/api/routes/readmodel.py):
    get_stats()                         — delegates to StatsService
    get_theses_list()                   — delegates to ThesisQueryService
    get_thesis_detail()                 — delegates to ThesisQueryService
    get_upcoming_catalysts()            — delegates to ThesisQueryService
    get_thesis_portfolio_aggregate()    — delegates to ThesisQueryService
    get_conviction_timeline()           — delegates to ThesisQueryService
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

# ---------------------------------------------------------------------------
# Cross-segment model imports — consolidated top-level.
# Guarded so the readmodel segment stays importable even when a peer segment
# hasn't been migrated yet (e.g. in unit-test environments).
# ---------------------------------------------------------------------------

try:
    from src.watchlist.models import Alert, AlertStatus, SignalEvent, WatchlistScan

    _WATCHLIST_MODELS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _WATCHLIST_MODELS_AVAILABLE = False
    Alert = AlertStatus = SignalEvent = WatchlistScan = None  # type: ignore[assignment,misc]

try:
    from src.briefing.models import BriefFeedback, BriefSnapshot

    _BRIEFING_MODELS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _BRIEFING_MODELS_AVAILABLE = False
    BriefFeedback = BriefSnapshot = None  # type: ignore[assignment,misc]


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
    # 2-6. Thesis queries — delegates to ThesisQueryService
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

    async def get_conviction_timeline(
        self,
        user_id: str,
        thesis_id: int,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """Conviction score series cho 1 thesis — dùng cho sparkline / trend chart.

        Delegates to ThesisQueryService.get_conviction_timeline().
        Trả về list rỗng nếu thesis không tồn tại hoặc không thuộc user.
        """
        return await self._thesis_query.get_conviction_timeline(
            user_id=user_id,
            thesis_id=thesis_id,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # 7. Latest scan snapshot — cross-segment (watchlist)
    # ------------------------------------------------------------------

    async def get_scan_latest(self, user_id: str) -> dict[str, Any] | None:
        if not _WATCHLIST_MODELS_AVAILABLE:
            logger.warning("get_scan_latest.import_error", detail="WatchlistScan model not available")
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
            logger.warning("get_scan_latest.db_error", error=str(exc), exc_info=True)
            return None

    # ------------------------------------------------------------------
    # 8. Latest brief snapshot — cross-segment (briefing)
    # ------------------------------------------------------------------

    async def get_brief_latest(self, user_id: str, phase: str = "morning") -> dict[str, Any] | None:
        if not _BRIEFING_MODELS_AVAILABLE:
            logger.warning("get_brief_latest.import_error", detail="BriefSnapshot model not available")
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
            logger.warning("get_brief_latest.db_error", error=str(exc), exc_info=True)
            return None

    async def get_brief_feedback_summary(
        self, user_id: str, days: int = 30
    ) -> dict[str, Any]:
        """Return brief feedback summary for user."""
        if not _BRIEFING_MODELS_AVAILABLE:
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
            logger.warning("get_brief_feedback_summary.db_error", error=str(exc), exc_info=True)
            return {
                "last_feedback_outcome": None,
                "last_feedback_at": None,
                "acted_rate_30d": None,
                "total_feedbacks_30d": 0,
            }

    # ------------------------------------------------------------------
    # 9. Triggered alerts — cross-segment (watchlist)
    # ------------------------------------------------------------------

    async def get_triggered_alerts(
        self,
        user_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Tra list alerts da fire (status=TRIGGERED), sap xep theo triggered_at desc."""
        if not _WATCHLIST_MODELS_AVAILABLE:
            logger.warning("get_triggered_alerts.import_error", detail="Alert model not available")
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
            logger.warning("get_triggered_alerts.db_error", error=str(exc), exc_info=True)
            return []

    # ------------------------------------------------------------------
    # 10. Recent signal events — grouped by ticker at DB level
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
          - GROUP BY ticker thực hiện tại DB layer (không fetch 500 rows về Python).
          - Mỗi ticker trả về 1 entry với fields:
              ticker, signal_types[], max_strength, max_confidence,
              count, first_seen, last_seen, source.
          - signal_types được collect bằng subquery riêng (chỉ DISTINCT types per ticker).
          - Loại bỏ noise: các signal lặp lại collapse thành count.

        Khi group_by_ticker=False:
          - Trả raw list (dùng cho detail view / per-ticker drill-down).
        """
        if not _WATCHLIST_MODELS_AVAILABLE:
            logger.warning("get_recent_signals.import_error", detail="SignalEvent model not available")
            return []

        try:
            since = datetime.now(UTC) - timedelta(days=days)

            if not group_by_ticker:
                # ---- Raw mode — backward compat ----
                stmt = (
                    select(SignalEvent)
                    .where(
                        SignalEvent.user_id == user_id,
                        SignalEvent.occurred_at >= since,
                    )
                    .order_by(SignalEvent.occurred_at.desc())
                    .limit(limit)
                )
                if ticker is not None:
                    stmt = stmt.where(SignalEvent.ticker == ticker.upper())

                rows = (await self._session.execute(stmt)).scalars().all()
                result = []
                for r in rows:
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

            # ---- Grouped mode: aggregate at DB level ----
            # Step 1: aggregate stats per ticker (max strength/confidence, count, time range)
            agg_stmt = (
                select(
                    SignalEvent.ticker,
                    func.max(SignalEvent.strength).label("max_strength"),
                    func.max(SignalEvent.confidence).label("max_confidence"),
                    func.count(SignalEvent.id).label("count"),
                    func.min(SignalEvent.occurred_at).label("first_seen"),
                    func.max(SignalEvent.occurred_at).label("last_seen"),
                    func.max(SignalEvent.source).label("source"),
                )
                .where(
                    SignalEvent.user_id == user_id,
                    SignalEvent.occurred_at >= since,
                )
                .group_by(SignalEvent.ticker)
                .order_by(
                    func.max(SignalEvent.strength).desc(),
                    func.count(SignalEvent.id).desc(),
                )
                .limit(limit)
            )
            if ticker is not None:
                agg_stmt = agg_stmt.where(SignalEvent.ticker == ticker.upper())

            agg_rows = (await self._session.execute(agg_stmt)).all()

            if not agg_rows:
                return []

            # Step 2: fetch distinct signal_types for the tickers we got
            tickers_in_result = [r.ticker for r in agg_rows]
            type_rows = (
                await self._session.execute(
                    select(SignalEvent.ticker, SignalEvent.signal_type)
                    .where(
                        SignalEvent.user_id == user_id,
                        SignalEvent.occurred_at >= since,
                        SignalEvent.ticker.in_(tickers_in_result),
                    )
                    .distinct()
                )
            ).all()

            # Build ticker → set[signal_type] map
            types_by_ticker: dict[str, set[str]] = {}
            for t, st in type_rows:
                types_by_ticker.setdefault(t, set()).add(st)

            # Step 3: assemble response — tz-safe datetime handling
            result = []
            for r in agg_rows:
                first_seen = r.first_seen
                last_seen = r.last_seen

                # Normalise naive datetimes returned by some DB drivers (e.g. asyncpg
                # without explicit timezone columns) to UTC-aware so callers always
                # get consistent ISO strings with offset.
                if first_seen is not None and first_seen.tzinfo is None:
                    first_seen = first_seen.replace(tzinfo=UTC)
                if last_seen is not None and last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=UTC)

                result.append({
                    "ticker": r.ticker,
                    "signal_types": sorted(types_by_ticker.get(r.ticker, set())),
                    "max_strength": round(r.max_strength or 0.0, 3),
                    "max_confidence": round(r.max_confidence or 0.0, 3),
                    "count": r.count,
                    "first_seen": first_seen.isoformat() if first_seen else None,
                    "last_seen": last_seen.isoformat() if last_seen else None,
                    "source": r.source,
                })

            return result

        except Exception as exc:
            logger.warning("get_recent_signals.db_error", error=str(exc), exc_info=True)
            return []

    # ------------------------------------------------------------------
    # 11-13. Backtesting — delegates to BacktestingService
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
    # 14. Portfolio — delegates to PortfolioQueryService
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
                        exc_info=True,
                    )
                    price_map = {}

        return await self._portfolio_query.get_portfolio(user_id, price_map=price_map)
