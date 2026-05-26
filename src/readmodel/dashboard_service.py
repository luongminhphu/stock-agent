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

Design rules:
- This class is a thin facade — no query logic lives here.
- SELECT-only: no writes, no business logic, no AI calls.
- All public methods are async and accept an AsyncSession (via constructor).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
    # 7. Latest scan snapshot — cross-segment (watchlist), cached 30s
    # ------------------------------------------------------------------

    async def get_scan_latest(self, user_id: str) -> dict[str, Any] | None:
        cached = _cache.get("scan_latest", user_id)
        if cached is not None:
            return cached

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

            async def _fetch_feedback() -> str | None:
                return (
                    await self._session.execute(
                        select(BriefFeedback.outcome)
                        .where(BriefFeedback.brief_snapshot_id == row.id)
                        .order_by(BriefFeedback.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()

            async def _parse_content() -> dict:
                try:
                    parsed = json.loads(row.content)
                    if not isinstance(parsed, dict):
                        return {"summary": row.content, "content": row.content}
                    return parsed
                except (json.JSONDecodeError, TypeError):
                    return {"summary": row.content, "content": row.content}

            feedback_outcome, parsed_content = await asyncio.gather(
                _fetch_feedback(),
                _parse_content(),
            )

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

            types_by_ticker: dict[str, set[str]] = {}
            for t, st in type_rows:
                types_by_ticker.setdefault(t, set()).add(st)

            result = []
            for r in agg_rows:
                first_seen = r.first_seen
                last_seen = r.last_seen

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

        # ---- Source 1: Triggered alerts --------------------------------
        if _WATCHLIST_MODELS_AVAILABLE:
            try:
                alert_rows = (
                    await self._session.execute(
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

                overdue_rows = (await self._session.execute(stmt)).all()

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
                    await self._session.execute(
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
                    await self._session.execute(
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
