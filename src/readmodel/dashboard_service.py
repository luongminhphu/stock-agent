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
    get_acted_tickers_recent()          — tickers user đã act trong N ngày gần nhất
    get_triggered_alerts()              — alerts da fire, chua xu ly (Alert)
    get_recent_signals()                — signal history per ticker (SignalEvent)
    get_verdict_accuracy()              — delegates to BacktestingService
    get_thesis_performances()           — delegates to BacktestingService
    get_price_snapshots()               — delegates to BacktestingService
    get_portfolio()                     — delegates to PortfolioQueryService
    get_attention_needed()              — aggregated attention panel (Wave B)
    get_intelligence()                  — intelligence snapshot (Gap 4)

Design rules:
- This class is a thin facade — no query logic lives here.
- SELECT-only: no writes, no business logic, no AI calls.
- All public methods are async and accept an AsyncSession (via constructor).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import case, func, literal_column, over, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.db import AsyncSessionLocal
from src.platform.logging import get_logger
from src.readmodel.backtesting_service import BacktestingService
from src.readmodel.cache import DashboardTTLCache
from src.readmodel.portfolio_query_service import PortfolioQueryService
from src.readmodel.schemas import AttentionItem, AttentionPanelResponse, AttentionUrgency
from src.readmodel.stats_service import StatsService
from src.readmodel.thesis_query_service import ThesisQueryService

logger = get_logger(__name__)

# Module-level cache — shared across all DashboardService instances within
# the same process. Keyed by (namespace, user_id, extra) for per-user isolation.
_cache = DashboardTTLCache()

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
    from src.briefing.models import BriefFeedback, BriefFeedbackOutcome, BriefSnapshot

    _BRIEFING_MODELS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _BRIEFING_MODELS_AVAILABLE = False
    BriefFeedback = BriefFeedbackOutcome = BriefSnapshot = None  # type: ignore[assignment,misc]

try:
    from src.thesis.models import Catalyst, CatalystStatus, Thesis, ThesisReview, ThesisStatus

    _THESIS_MODELS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _THESIS_MODELS_AVAILABLE = False
    Catalyst = CatalystStatus = Thesis = ThesisReview = ThesisStatus = None  # type: ignore[assignment,misc]

# Gap 4: IntelligenceSnapshotStore — in-process read, no DB, no AI call.
# Guarded: store is only available after bootstrap() has been called.
try:
    from src.readmodel.intelligence_snapshot import get_intelligence_snapshot

    _INTELLIGENCE_SNAPSHOT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _INTELLIGENCE_SNAPSHOT_AVAILABLE = False


class QuoteBatchReader(Protocol):
    """Minimal batch-quote interface required by DashboardService."""

    async def get_quotes(self, tickers: list[str]): ...  # noqa: D102


# ---------------------------------------------------------------------------
# Attention panel constants
# ---------------------------------------------------------------------------

_OVERDUE_REVIEW_DAYS: int = 14          # thesis without review for > N days
_UPCOMING_CATALYST_HOURS: int = 72      # catalyst deadline within N hours
_STOP_LOSS_PROXIMITY_PCT: float = 3.0   # price within N% of stop_loss
_ATTENTION_CACHE_TTL_SECS: int = 30     # cache TTL (panel is a hint, not a signal)

_URGENCY_ORDER = {
    AttentionUrgency.CRITICAL: 0,
    AttentionUrgency.HIGH: 1,
    AttentionUrgency.MEDIUM: 2,
}


def _ensure_utc(dt: datetime) -> datetime:
    """Return dt as a UTC-aware datetime.

    - If dt is already timezone-aware, convert to UTC via astimezone().
    - If dt is naive, assume UTC and attach tzinfo (no conversion needed).

    Never use .replace(tzinfo=UTC) on aware datetimes — that silently overwrites
    the offset without adjusting the wall-clock value.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._stats = StatsService(session)
        self._thesis_query = ThesisQueryService(session)
        self._portfolio_query = PortfolioQueryService(session)
        self._backtesting = BacktestingService(session)

    # ------------------------------------------------------------------
    # 1. Stats — delegates to StatsService, cached 60s
    # ------------------------------------------------------------------

    async def get_stats(self, user_id: str) -> dict[str, Any]:
        cached = _cache.get("stats", user_id)
        if cached is not None:
            return cached
        result = await self._stats.get_stats(user_id)
        _cache.set("stats", user_id, result)
        return result

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
        try:
            return await self._thesis_query.get_upcoming_catalysts(user_id, days=days)
        except Exception as exc:
            logger.warning(
                "dashboard_service.get_upcoming_catalysts.error",
                user_id=user_id,
                days=days,
                error=str(exc),
                exc_info=True,
            )
            return []

    async def get_thesis_portfolio_aggregate(
        self,
        user_id: str,
        price_map: dict[str, float] | None = None,
        position_map: dict[str, tuple[float, float]] | None = None,
    ) -> dict[str, Any]:
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
        return await self._thesis_query.get_conviction_timeline(
            user_id=user_id,
            thesis_id=thesis_id,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # 7. Latest scan snapshot — uses isolated session to prevent ISCE.
    #
    # Root cause: AsyncSession does not support concurrent operations.
    # DashboardService methods (get_attention_needed, get_theses_list, etc.)
    # execute multiple sequential queries on self._session. If get_scan_latest
    # is called while self._session is mid-connection-provisioning (e.g. the
    # first query in a request that hasn't resolved its greenlet yet), SQLAlchemy
    # raises InvalidRequestError (ISCE).
    #
    # Fix: open a short-lived dedicated session for this read, independent of
    # the shared request session. The result is cached for 30s so the extra
    # connection overhead is negligible in practice.
    # ------------------------------------------------------------------

    async def get_scan_latest(self, user_id: str) -> dict[str, Any] | None:
        cached = _cache.get("scan_latest", user_id)
        if cached is not None:
            return cached

        if not _WATCHLIST_MODELS_AVAILABLE:
            logger.warning("get_scan_latest.import_error", detail="WatchlistScan model not available")
            return None

        try:
            # Use an isolated session — never reuse self._session here.
            # See module docstring for root cause explanation.
            async with AsyncSessionLocal() as session:
                row = (
                    await session.execute(
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

            result = {
                **parsed_summary,
                "id": row.id,
                "user_id": row.user_id,
                "summary": row.summary,
                "scanned_at": row.scanned_at.isoformat() if row.scanned_at else None,
            }
            _cache.set("scan_latest", user_id, result)
            return result
        except Exception as exc:
            logger.warning("get_scan_latest.db_error", error=str(exc), exc_info=True)
            return None

    # ------------------------------------------------------------------
    # 8. Latest brief snapshot + feedback — cached 30s
    # ------------------------------------------------------------------

    async def get_brief_latest(self, user_id: str, phase: str = "morning") -> dict[str, Any] | None:
        cache_extra = phase
        cached = _cache.get("brief_latest", user_id, extra=cache_extra)
        if cached is not None:
            return cached

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

            # Fetch feedback outcome sequentially — AsyncSession does not support
            # concurrent operations (asyncio.gather on the same session raises ISCE).
            feedback_outcome = (
                await self._session.execute(
                    select(BriefFeedback.outcome)
                    .where(BriefFeedback.brief_snapshot_id == row.id)
                    .order_by(BriefFeedback.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            # Pure CPU work — no DB call needed, no async required.
            try:
                parsed_content = json.loads(row.content)
                if not isinstance(parsed_content, dict):
                    parsed_content = {"summary": row.content, "content": row.content}
            except (json.JSONDecodeError, TypeError):
                parsed_content = {"summary": row.content, "content": row.content}

            result = {
                **parsed_content,
                "id": row.id,
                "user_id": row.user_id,
                "phase": row.phase,
                "content": row.content,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "feedback_outcome": feedback_outcome,
            }
            _cache.set("brief_latest", user_id, result, extra=cache_extra)
            return result
        except Exception as exc:
            logger.warning("get_brief_latest.db_error", error=str(exc), exc_info=True)
            return None

    async def get_brief_feedback_summary(
        self, user_id: str, days: int = 30
    ) -> dict[str, Any]:
        """Return feedback summary using a single DB-level aggregation query.

        Previous implementation: 2 round-trips — one full-object fetch for
        latest feedback, one full outcome column scan returned to Python for
        len() + sum(). O(N) Python loop on potentially large result sets.

        Current implementation: 1 query using:
          - ROW_NUMBER() OVER (ORDER BY created_at DESC) to identify latest row
          - COUNT(*) FILTER (WHERE created_at >= since) for total_feedbacks_30d
          - COUNT(*) FILTER (WHERE created_at >= since AND outcome = 'acted')
            for acted_count used to compute acted_rate_30d
          - MAX(CASE WHEN rank=1 THEN ...) to extract latest outcome/created_at

        All aggregation happens in PostgreSQL. Zero Python counting loops.
        """
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
            since = datetime.now(UTC) - timedelta(days=days)

            # Subquery: annotate each row with row_number (latest first).
            # Using literal_column for the window function label so SQLAlchemy
            # doesn't need to know the column type at compile time.
            ranked_sq = (
                select(
                    BriefFeedback.outcome,
                    BriefFeedback.created_at,
                    over(
                        func.row_number(),
                        order_by=BriefFeedback.created_at.desc(),
                    ).label("rn"),
                )
                .where(BriefFeedback.user_id == user_id)
                .subquery()
            )

            # Single aggregation pass over the ranked subquery.
            # COUNT FILTER: standard SQL:2003, supported by PostgreSQL 9.4+.
            stmt = select(
                # latest feedback fields (rank = 1)
                func.max(
                    case((ranked_sq.c.rn == 1, ranked_sq.c.outcome), else_=None)
                ).label("last_outcome"),
                func.max(
                    case((ranked_sq.c.rn == 1, ranked_sq.c.created_at), else_=None)
                ).label("last_created_at"),
                # 30-day window counts
                func.count(literal_column("1")).filter(
                    ranked_sq.c.created_at >= since
                ).label("total_30d"),
                func.count(literal_column("1")).filter(
                    ranked_sq.c.created_at >= since,
                    ranked_sq.c.outcome == "acted",
                ).label("acted_30d"),
            )

            row = (await self._session.execute(stmt)).one_or_none()

            if row is None:
                return {
                    "last_feedback_outcome": None,
                    "last_feedback_at": None,
                    "acted_rate_30d": None,
                    "total_feedbacks_30d": 0,
                }

            total = row.total_30d or 0
            acted = row.acted_30d or 0
            acted_rate = round(acted / total, 3) if total > 0 else None

            last_at = row.last_created_at
            if last_at is not None:
                last_at = _ensure_utc(last_at)

            return {
                "last_feedback_outcome": row.last_outcome,
                "last_feedback_at": last_at.isoformat() if last_at else None,
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

    async def get_acted_tickers_recent(
        self,
        user_id: str,
        days: int = 3,
    ) -> list[str]:
        """Return distinct tickers from briefs the user acted on in the last N days."""
        if not _BRIEFING_MODELS_AVAILABLE:
            return []

        cache_extra = str(days)
        cached = _cache.get("acted_tickers", user_id, extra=cache_extra)
        if cached is not None:
            return cached

        try:
            since = datetime.now(UTC) - timedelta(days=days)

            rows = (
                await self._session.execute(
                    select(BriefSnapshot.tickers)
                    .join(
                        BriefFeedback,
                        BriefFeedback.brief_snapshot_id == BriefSnapshot.id,
                    )
                    .where(
                        BriefFeedback.user_id == user_id,
                        BriefSnapshot.user_id == user_id,
                        BriefFeedback.outcome == BriefFeedbackOutcome.ACTED,
                        BriefFeedback.created_at >= since,
                    )
                    .distinct()
                )
            ).scalars().all()

            tickers: set[str] = set()
            for csv in rows:
                if not csv:
                    continue
                for t in csv.split(","):
                    cleaned = t.strip().upper()
                    if cleaned:
                        tickers.add(cleaned)

            result = sorted(tickers)
            _cache.set("acted_tickers", user_id, result, extra=cache_extra)
            logger.info(
                "get_acted_tickers_recent.done",
                user_id=user_id,
                days=days,
                tickers=result,
            )
            return result

        except Exception as exc:
            logger.warning(
                "get_acted_tickers_recent.db_error",
                user_id=user_id,
                days=days,
                error=str(exc),
                exc_info=True,
            )
            return []

    # ------------------------------------------------------------------
    # 9. Triggered alerts — cross-segment (watchlist)
    # ------------------------------------------------------------------

    async def get_triggered_alerts(
        self,
        user_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
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
    # 10. Recent signal events — grouped by ticker at DB level, cached 30s
    #
    # group_by_ticker=True path (default): single query using
    # array_agg(DISTINCT signal_type ORDER BY signal_type) — PostgreSQL.
    # Eliminates the previous second round-trip that fetched distinct
    # signal_types per ticker in a separate SELECT.
    #
    # The aggregated array is returned by PostgreSQL as a native Python list;
    # we sort and deduplicate defensively on the Python side as well.
    # ------------------------------------------------------------------

    async def get_recent_signals(
        self,
        user_id: str,
        ticker: str | None = None,
        days: int = 7,
        limit: int = 50,
        group_by_ticker: bool = True,
    ) -> list[dict[str, Any]]:
        if not _WATCHLIST_MODELS_AVAILABLE:
            logger.warning("get_recent_signals.import_error", detail="SignalEvent model not available")
            return []

        try:
            since = datetime.now(UTC) - timedelta(days=days)

            if not group_by_ticker:
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

            cache_extra = f"{ticker or ''}:{days}"
            cached = _cache.get("recent_signals", user_id, extra=cache_extra)
            if cached is not None:
                return cached

            # Single query: aggregate metrics + collect distinct signal_types
            # via array_agg(DISTINCT ...) in one GROUP BY pass.
            # PostgreSQL returns the aggregated column as a native Python list.
            agg_stmt = (
                select(
                    SignalEvent.ticker,
                    func.max(SignalEvent.strength).label("max_strength"),
                    func.max(SignalEvent.confidence).label("max_confidence"),
                    func.count(SignalEvent.id).label("count"),
                    func.min(SignalEvent.occurred_at).label("first_seen"),
                    func.max(SignalEvent.occurred_at).label("last_seen"),
                    func.max(SignalEvent.source).label("source"),
                    func.array_agg(
                        SignalEvent.signal_type.distinct()
                    ).label("signal_types_agg"),
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

            result = []
            for r in agg_rows:
                first_seen = r.first_seen
                last_seen = r.last_seen

                if first_seen is not None and first_seen.tzinfo is None:
                    first_seen = first_seen.replace(tzinfo=UTC)
                if last_seen is not None and last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=UTC)

                # array_agg returns a list or None; sort + deduplicate defensively.
                raw_types = r.signal_types_agg or []
                signal_types = sorted({st for st in raw_types if st is not None})

                result.append({
                    "ticker": r.ticker,
                    "signal_types": signal_types,
                    "max_strength": round(r.max_strength or 0.0, 3),
                    "max_confidence": round(r.max_confidence or 0.0, 3),
                    "count": r.count,
                    "first_seen": first_seen.isoformat() if first_seen else None,
                    "last_seen": last_seen.isoformat() if last_seen else None,
                    "source": r.source,
                })

            _cache.set("recent_signals", user_id, result, extra=cache_extra)
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

    # ------------------------------------------------------------------
    # 15. Attention Panel — "Việc cần làm hôm nay" (Wave B)
    #
    # Uses an isolated AsyncSessionLocal() session — NOT self._session.
    #
    # Root cause: AsyncSession does not support concurrent or interleaved
    # operations. When the route layer calls get_attention_needed alongside
    # other DashboardService methods that share self._session, the session
    # may still be mid-connection-provisioning, causing SQLAlchemy
    # InvalidRequestError (ISCE) — same root cause as get_scan_latest.
    #
    # Fix: all 4 attention sources run inside a single dedicated session
    # that is opened and closed within this method. The result is cached
    # for 30s so the extra connection overhead is negligible in practice.
    # ------------------------------------------------------------------

    async def get_attention_needed(
        self,
        user_id: str,
        price_map: dict[str, float] | None = None,
        limit: int = 20,
    ) -> AttentionPanelResponse:
        """Aggregate attention items từ 4 nguồn, sắp xếp critical → high → medium.

        Sources:
          1. triggered_alerts      — alerts đã fire, chưa dismiss
          2. overdue_reviews       — thesis active, không có AI review > 14 ngày
          3. upcoming_catalysts    — catalyst PENDING trong 72h tới
          4. stop_loss_proximity   — giá hiện tại cách stop_loss <= 3%

        price_map: injected từ route layer (QuoteService) — service không tự fetch.
        Partial results returned nếu một source fail — không raise lên HTTP layer.
        Cached 30s per (user_id, limit).
        """
        cache_extra = str(limit)
        cached = _cache.get("attention", user_id, extra=cache_extra)
        if cached is not None:
            return cached  # type: ignore[return-value]

        now = datetime.now(UTC)
        items: list[AttentionItem] = []
        seen: set[tuple[str, str, int | None]] = set()  # dedup key: (kind, ticker, thesis_id)

        def _add(item: AttentionItem) -> None:
            key = (item.kind, item.ticker, item.thesis_id)
            if key not in seen:
                seen.add(key)
                items.append(item)

        # Open a dedicated session for all 4 sources — never reuse self._session.
        # See method docstring for root cause explanation.
        async with AsyncSessionLocal() as session:

            # ---- Source 1: Triggered alerts --------------------------------
            if _WATCHLIST_MODELS_AVAILABLE:
                try:
                    alert_rows = (
                        await session.execute(
                            select(Alert)
                            .where(
                                Alert.user_id == user_id,
                                Alert.status == AlertStatus.TRIGGERED,
                            )
                            .order_by(Alert.triggered_at.desc())
                            .limit(50)
                        )
                    ).scalars().all()

                    for r in alert_rows:
                        priority = getattr(r, "priority", "medium") or "medium"
                        urgency = (
                            AttentionUrgency.CRITICAL
                            if priority == "critical"
                            else AttentionUrgency.HIGH
                            if priority == "high"
                            else AttentionUrgency.MEDIUM
                        )
                        triggered_at = r.triggered_at or now
                        if triggered_at.tzinfo is None:
                            triggered_at = triggered_at.replace(tzinfo=UTC)
                        label = getattr(r, "label", None) or str(getattr(r, "condition_type", "alert"))
                        _add(AttentionItem(
                            kind="triggered_alert",
                            ticker=r.ticker,
                            thesis_id=getattr(r, "thesis_id", None),
                            message=f"Alert: {label} @ {r.ticker}",
                            urgency=urgency,
                            ts=triggered_at,
                            metadata={
                                "alert_id": r.id,
                                "condition_type": str(getattr(r, "condition_type", "")),
                                "triggered_price": getattr(r, "triggered_price", None),
                                "threshold": getattr(r, "threshold", None),
                            },
                        ))
                except Exception as exc:
                    logger.warning("attention.source1_alerts.error", error=str(exc), exc_info=True)

            # ---- Source 2: Overdue reviews ---------------------------------
            if _THESIS_MODELS_AVAILABLE:
                try:
                    overdue_cutoff = now - timedelta(days=_OVERDUE_REVIEW_DAYS)

                    # Subquery: last reviewed_at per thesis
                    last_review_sq = (
                        select(
                            ThesisReview.thesis_id,
                            func.max(ThesisReview.reviewed_at).label("last_reviewed_at"),
                        )
                        .group_by(ThesisReview.thesis_id)
                        .subquery()
                    )

                    # Active theses where last review is older than cutoff OR has no review
                    stmt = (
                        select(
                            Thesis.id,
                            Thesis.ticker,
                            Thesis.title,
                            Thesis.created_at,
                            last_review_sq.c.last_reviewed_at,
                        )
                        .outerjoin(last_review_sq, last_review_sq.c.thesis_id == Thesis.id)
                        .where(
                            Thesis.user_id == user_id,
                            Thesis.status == ThesisStatus.ACTIVE,
                        )
                        .where(
                            (last_review_sq.c.last_reviewed_at == None)  # noqa: E711
                            | (last_review_sq.c.last_reviewed_at < overdue_cutoff)
                        )
                        .order_by(
                            last_review_sq.c.last_reviewed_at.asc().nulls_first()
                        )
                        .limit(20)
                    )

                    overdue_rows = (await session.execute(stmt)).all()

                    for row in overdue_rows:
                        last_reviewed = row.last_reviewed_at
                        if last_reviewed is not None:
                            # Use _ensure_utc: aware → astimezone(UTC), naive → attach UTC
                            last_reviewed = _ensure_utc(last_reviewed)

                        created_at_utc = _ensure_utc(row.created_at)

                        if last_reviewed is None:
                            days_overdue = (now - created_at_utc).days
                            msg = f"{row.ticker}: chưa từng được review AI"
                        else:
                            days_overdue = (now - last_reviewed).days
                            msg = f"{row.ticker}: review AI cách đây {days_overdue} ngày"

                        _add(AttentionItem(
                            kind="overdue_review",
                            ticker=row.ticker,
                            thesis_id=row.id,
                            message=msg,
                            urgency=AttentionUrgency.HIGH,
                            ts=last_reviewed or created_at_utc,
                            metadata={"days_overdue": days_overdue},
                        ))
                except Exception as exc:
                    logger.warning("attention.source2_overdue.error", error=str(exc), exc_info=True)

            # ---- Source 3: Upcoming catalysts within 72h -------------------
            if _THESIS_MODELS_AVAILABLE:
                try:
                    cutoff_near = now + timedelta(hours=_UPCOMING_CATALYST_HOURS)

                    upcoming_rows = (
                        await session.execute(
                            select(
                                Catalyst.id,
                                Catalyst.thesis_id,
                                Catalyst.description,
                                Catalyst.expected_date,
                                Thesis.ticker,
                                Thesis.title,
                            )
                            .join(Thesis, Thesis.id == Catalyst.thesis_id)
                            .where(
                                Thesis.user_id == user_id,
                                Thesis.status == ThesisStatus.ACTIVE,
                                Catalyst.status == CatalystStatus.PENDING,
                                Catalyst.expected_date >= now,
                                Catalyst.expected_date <= cutoff_near,
                            )
                            .order_by(Catalyst.expected_date.asc())
                            .limit(20)
                        )
                    ).all()

                    for row in upcoming_rows:
                        expected = row.expected_date
                        if expected is not None:
                            expected = _ensure_utc(expected)
                        hours_left = round((expected - now).total_seconds() / 3600, 1) if expected else None
                        desc = (row.description or "")[:80]
                        msg = (
                            f"{row.ticker}: catalyst '{desc}' trong {hours_left}h"
                            if hours_left is not None
                            else f"{row.ticker}: catalyst sắp đến hạn"
                        )
                        _add(AttentionItem(
                            kind="upcoming_catalyst",
                            ticker=row.ticker,
                            thesis_id=row.thesis_id,
                            message=msg,
                            urgency=AttentionUrgency.HIGH,
                            ts=expected or now,
                            metadata={
                                "catalyst_id": row.id,
                                "hours_left": hours_left,
                                "description": row.description,
                            },
                        ))
                except Exception as exc:
                    logger.warning("attention.source3_catalysts.error", error=str(exc), exc_info=True)

            # ---- Source 4: Stop-loss proximity (requires price_map) --------
            if price_map and _THESIS_MODELS_AVAILABLE:
                try:
                    sl_rows = (
                        await session.execute(
                            select(
                                Thesis.id,
                                Thesis.ticker,
                                Thesis.title,
                                Thesis.stop_loss,
                                Thesis.created_at,
                            )
                            .where(
                                Thesis.user_id == user_id,
                                Thesis.status == ThesisStatus.ACTIVE,
                                Thesis.stop_loss != None,  # noqa: E711
                            )
                        )
                    ).all()

                    for row in sl_rows:
                        current = price_map.get(row.ticker)
                        if current is None or row.stop_loss is None or row.stop_loss <= 0:
                            continue
                        distance_pct = abs(current - row.stop_loss) / row.stop_loss * 100
                        if distance_pct <= _STOP_LOSS_PROXIMITY_PCT:
                            created_at_utc = _ensure_utc(row.created_at)
                            _add(AttentionItem(
                                kind="stop_loss_proximity",
                                ticker=row.ticker,
                                thesis_id=row.id,
                                message=(
                                    f"{row.ticker}: giá hiện tại cách stop_loss "
                                    f"{distance_pct:.1f}% "
                                    f"(giá: {current:,.0f} | SL: {row.stop_loss:,.0f})"
                                ),
                                urgency=AttentionUrgency.CRITICAL,
                                ts=now,
                                metadata={
                                    "current_price": current,
                                    "stop_loss": row.stop_loss,
                                    "distance_pct": round(distance_pct, 2),
                                },
                            ))
                except Exception as exc:
                    logger.warning("attention.source4_stoploss.error", error=str(exc), exc_info=True)

        # ---- Sort: critical → high → medium, stable ts desc within tier ---
        items.sort(key=lambda x: (_URGENCY_ORDER.get(x.urgency, 99), -(x.ts.timestamp())))
        items = items[:limit]

        response = AttentionPanelResponse(
            user_id=user_id,
            generated_at=now,
            items=items,
            total=len(items),
        )
        _cache.set("attention", user_id, response, extra=cache_extra)
        return response

    # ------------------------------------------------------------------
    # 16. Intelligence snapshot — Gap 4 (readmodel)
    #
    # Reads from IntelligenceSnapshotStore (in-process, no DB, no AI call).
    # The store is populated by IntelligenceSnapshotSubscriber which listens
    # to IntelligenceEngineCompletedEvent — wired in bootstrap.py.
    #
    # Returns None when:
    #   - store not yet populated (engine hasn't run yet today)
    #   - bootstrap() hasn't been called (ImportError guard)
    #   - store raises unexpectedly
    #
    # Cached 30s — same TTL as other lightweight facade methods.
    # is_stale=True when snapshot is older than store's staleness threshold.
    # ------------------------------------------------------------------

    async def get_intelligence(self, user_id: str) -> dict[str, Any] | None:
        """Return the latest intelligence snapshot for user, or None if unavailable.

        Output shape:
            overall_verdict:   str | None   — e.g. "CAUTIOUS_BULLISH"
            confidence:        float | None — 0.0–1.0
            market_context:    str | None   — brief market narrative
            priority_actions:  list[dict]   — [{ticker, action_text, urgency, reasoning}]
            risk_flags:        list[dict]   — [{description, severity}]
            watch_list:        list[str]    — tickers to monitor
            is_stale:          bool         — True if snapshot older than threshold
            generated_at:      str | None   — ISO8601 UTC timestamp
        """
        cached = _cache.get("intelligence", user_id)
        if cached is not None:
            return cached

        if not _INTELLIGENCE_SNAPSHOT_AVAILABLE:
            logger.warning(
                "get_intelligence.unavailable",
                user_id=user_id,
                reason="intelligence_snapshot module not importable",
            )
            return None

        try:
            store = get_intelligence_snapshot()
            snap_result = await store.get(user_id)
            if snap_result is None:
                logger.debug("get_intelligence.no_snapshot", user_id=user_id)
                return None

            report, is_stale = snap_result
            generated_at = store.last_updated_at(user_id)

            def _serialize_actions(actions: list | None) -> list[dict]:
                if not actions:
                    return []
                out = []
                for a in actions:
                    out.append({
                        "ticker": str(getattr(a, "ticker", "") or "").upper() or None,
                        "action_text": str(getattr(a, "action_text", "") or "")[:300],
                        "urgency": str(getattr(a, "urgency", "medium") or "medium").lower(),
                        "reasoning": str(getattr(a, "reasoning", "") or "")[:500] or None,
                    })
                return out

            def _serialize_risk_flags(flags: list | None) -> list[dict]:
                if not flags:
                    return []
                out = []
                for f in flags:
                    out.append({
                        "description": str(getattr(f, "description", "") or "")[:300],
                        "severity": str(getattr(f, "severity", "low") or "low").lower(),
                    })
                return out

            result: dict[str, Any] = {
                "overall_verdict": str(getattr(report, "overall_verdict", "") or "") or None,
                "confidence": float(getattr(report, "confidence", 0.0) or 0.0),
                "market_context": str(getattr(report, "market_context", "") or "")[:500] or None,
                "priority_actions": _serialize_actions(getattr(report, "priority_actions", None)),
                "risk_flags": _serialize_risk_flags(getattr(report, "risk_flags", None)),
                "watch_list": [
                    str(t).upper()
                    for t in (getattr(report, "watch_list", None) or [])
                    if t
                ],
                "is_stale": is_stale,
                "generated_at": generated_at.isoformat() if generated_at else None,
            }

            _cache.set("intelligence", user_id, result)
            logger.debug(
                "get_intelligence.ok",
                user_id=user_id,
                is_stale=is_stale,
                priority_actions=len(result["priority_actions"]),
                risk_flags=len(result["risk_flags"]),
            )
            return result

        except Exception as exc:
            logger.warning(
                "get_intelligence.error",
                user_id=user_id,
                error=str(exc),
                exc_info=True,
            )
            return None
