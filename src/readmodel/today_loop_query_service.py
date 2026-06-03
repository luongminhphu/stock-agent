"""TodayLoopQueryService — aggregate today’s signals into a single actionable view.

Owner: readmodel segment.

Sources (in priority order):
    1. IntelligenceSnapshotStore  → priority_actions + risk_flags (in-process, no DB)
    2. WatchlistAlert DB rows      → WATCHLIST_SCAN signals (already snooze-filtered
       by snapshot._fetch_alerts, but we query directly here for freshness)
    3. SchedulerMonitor            → engine health status map

No new AI calls. No cross-segment domain logic.
All sources degrade gracefully — partial failure returns whatever was collected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.platform.scheduler_monitor import get_monitor
from src.readmodel.intelligence_snapshot import get_intelligence_snapshot

_logger = get_logger(__name__)


SignalSource = Literal[
    "THESIS_DRIFT",
    "WATCHLIST_SCAN",
    "PROACTIVE",
    "REMINDER",
    "INTELLIGENCE",
    "BRIEF",
    "OTHER",
]

SignalSeverity = Literal["HIGH", "MEDIUM", "LOW"]

# Maps PriorityAction.urgency / RiskFlag.severity strings → SignalSeverity
_URGENCY_MAP: dict[str, SignalSeverity] = {
    "critical": "HIGH",
    "high":     "HIGH",
    "medium":   "MEDIUM",
    "low":      "LOW",
}


@dataclass
class TodaySignal:
    id: str
    ticker: str
    source: SignalSource
    severity: SignalSeverity
    created_at: datetime
    headline: str
    details: str | None = None
    action_hint: str | None = None
    link_type: str | None = None
    link_target: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class EngineStatus:
    last_run: datetime | None
    ok: bool
    consecutive_failures: int = 0


@dataclass
class TodayLoopSummary:
    total_signals: int
    by_source: dict[str, int]


@dataclass
class TodayLoopResult:
    date: date
    generated_at: datetime
    summary: TodayLoopSummary
    top_actions: list[TodaySignal]   # HIGH severity only, max 5
    signals: list[TodaySignal]       # all signals sorted by severity + created_at
    engine_status: dict[str, EngineStatus]
    snapshot_is_stale: bool = False
    snapshot_generated_at: datetime | None = None


class TodayLoopQueryService:
    """Aggregate today’s signals and scheduler health into a single dashboard view.

    Usage::

        svc = TodayLoopQueryService(session)
        result = await svc.get_today_loop(user_id)
    """

    _SCHEDULER_TASKS = (
        "briefing.morning",
        "intelligence_engine.morning",
        "proactive_watch.morning",
        "reminder.daily",
    )

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._monitor = get_monitor()
        self._snapshot_store = get_intelligence_snapshot()

    async def get_today_loop(self, user_id: str) -> TodayLoopResult:
        """Return a fully-hydrated TodayLoopResult for the given user.

        Aggregates:
        - Intelligence priority actions + risk flags from the snapshot store
        - Active watchlist alerts from DB (not snoozed, triggered today)
        - Scheduler health for all engine tasks

        Never raises. On partial failure, returns whatever was collected.
        """
        now = datetime.now(UTC)
        signals: list[TodaySignal] = []
        snapshot_is_stale = False
        snapshot_generated_at: datetime | None = None

        # ————————————————————————————————————————————————————
        # Source 1: IntelligenceSnapshotStore — priority_actions + risk_flags
        # ————————————————————————————————————————————————————
        try:
            snap_result = await self._snapshot_store.get(user_id)
            if snap_result is not None:
                report, is_stale = snap_result
                snapshot_is_stale = is_stale
                snapshot_generated_at = self._snapshot_store.last_updated_at(user_id)

                # priority_actions → INTELLIGENCE signals
                for idx, action in enumerate(report.priority_actions or []):
                    severity = _URGENCY_MAP.get(
                        str(getattr(action, "urgency", "medium")).lower(), "MEDIUM"
                    )
                    ticker = getattr(action, "ticker", "") or ""
                    signals.append(TodaySignal(
                        id=f"intel_action_{idx}",
                        ticker=ticker.upper(),
                        source="INTELLIGENCE",
                        severity=severity,
                        created_at=snapshot_generated_at or now,
                        headline=str(getattr(action, "action_text", "") or "")[:200],
                        details=str(getattr(action, "reasoning", "") or "")[:400] or None,
                        action_hint=str(getattr(action, "action_text", "") or "")[:120] or None,
                        link_type="thesis" if ticker else None,
                        link_target=ticker or None,
                        tags=["intelligence", severity.lower()],
                    ))

                # risk_flags → INTELLIGENCE risk signals
                for idx, flag in enumerate(report.risk_flags or []):
                    flag_severity = _URGENCY_MAP.get(
                        str(getattr(flag, "severity", "low")).lower(), "LOW"
                    )
                    signals.append(TodaySignal(
                        id=f"intel_risk_{idx}",
                        ticker="",
                        source="INTELLIGENCE",
                        severity=flag_severity,
                        created_at=snapshot_generated_at or now,
                        headline=str(getattr(flag, "description", "") or "")[:200],
                        details=None,
                        action_hint=None,
                        tags=["risk", flag_severity.lower()],
                    ))

        except Exception as exc:
            _logger.warning(
                "today_loop.snapshot_source_failed",
                user_id=user_id,
                error=str(exc),
            )

        # ————————————————————————————————————————————————————
        # Source 2: WatchlistAlert DB — triggered today, not dismissed
        # Snooze-filtered by snapshot._fetch_alerts already, but we query
        # directly here for freshness (snapshot may be up to 300s old).
        # ————————————————————————————————————————————————————
        try:
            from src.watchlist.models import Alert, WatchlistItem  # type: ignore[import]

            today_start = datetime.now(UTC).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

            # Load snoozed tickers to filter out (same pattern as snapshot.py)
            snoozed_tickers: set[str] = set()
            try:
                snoozed_rows = (
                    await self._session.execute(
                        select(WatchlistItem.ticker).where(
                            WatchlistItem.user_id == user_id,
                            WatchlistItem.snoozed_until.isnot(None),
                            WatchlistItem.snoozed_until > now,
                        )
                    )
                ).scalars().all()
                snoozed_tickers = {t.upper() for t in snoozed_rows}
            except Exception:
                pass

            alert_rows = (
                await self._session.execute(
                    select(Alert).where(
                        Alert.user_id == user_id,
                        Alert.triggered_at >= today_start,
                        Alert.dismissed_at.is_(None),
                    )
                    .order_by(Alert.triggered_at.desc())
                    .limit(30)
                )
            ).scalars().all()

            for idx, row in enumerate(alert_rows):
                if row.ticker.upper() in snoozed_tickers:
                    continue
                alert_type = (row.alert_type or "").lower()
                severity: SignalSeverity = (
                    "HIGH" if any(k in alert_type for k in ("breach", "stop", "critical"))
                    else "MEDIUM" if any(k in alert_type for k in ("volume", "trend", "cross"))
                    else "LOW"
                )
                triggered_at = row.triggered_at
                if triggered_at is not None and triggered_at.tzinfo is None:
                    triggered_at = triggered_at.replace(tzinfo=UTC)
                signals.append(TodaySignal(
                    id=f"alert_{row.id}_{idx}",
                    ticker=row.ticker.upper(),
                    source="WATCHLIST_SCAN",
                    severity=severity,
                    created_at=triggered_at or now,
                    headline=f"{row.ticker}: {row.alert_type or 'Alert triggered'}",
                    details=getattr(row, "note", None),
                    action_hint="Review alert and update watchlist",
                    link_type="watchlist",
                    link_target=row.ticker.upper(),
                    tags=["watchlist", alert_type or "alert"],
                ))

        except Exception as exc:
            _logger.warning(
                "today_loop.alert_source_failed",
                user_id=user_id,
                error=str(exc),
            )

        # ————————————————————————————————————————————————————
        # Sort: HIGH first, then by created_at desc within same severity
        # ————————————————————————————————————————————————————
        _SEV_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        signals.sort(key=lambda s: (_SEV_ORDER.get(s.severity, 9), -s.created_at.timestamp()))

        # ————————————————————————————————————————————————————
        # Summary
        # ————————————————————————————————————————————————————
        by_source: dict[str, int] = {}
        for sig in signals:
            by_source[sig.source] = by_source.get(sig.source, 0) + 1

        summary = TodayLoopSummary(
            total_signals=len(signals),
            by_source=by_source,
        )

        top_actions = [s for s in signals if s.severity == "HIGH"][:5]

        # ————————————————————————————————————————————————————
        # Source 3: SchedulerMonitor — engine health
        # ————————————————————————————————————————————————————
        engine_status: dict[str, EngineStatus] = {}
        for task in self._SCHEDULER_TASKS:
            try:
                stats = self._monitor.get_task_stats(task)
                engine_status[task.replace(".", "_")] = EngineStatus(
                    last_run=stats.last_run,
                    ok=stats.consecutive_failures == 0,
                    consecutive_failures=stats.consecutive_failures,
                )
            except Exception:
                engine_status[task.replace(".", "_")] = EngineStatus(
                    last_run=None,
                    ok=False,
                    consecutive_failures=0,
                )

        _logger.debug(
            "today_loop.built",
            user_id=user_id,
            total_signals=len(signals),
            top_actions=len(top_actions),
            snapshot_is_stale=snapshot_is_stale,
        )

        return TodayLoopResult(
            date=date.today(),
            generated_at=now,
            summary=summary,
            top_actions=top_actions,
            signals=signals,
            engine_status=engine_status,
            snapshot_is_stale=snapshot_is_stale,
            snapshot_generated_at=snapshot_generated_at,
        )
