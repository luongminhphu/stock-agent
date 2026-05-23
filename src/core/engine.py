"""Intelligence Engine — core orchestrator.

Owner: core segment.

Wave 1 (this file):
    - Build SystemSnapshot from cross-segment DB queries
    - Emit IntelligenceEngineRequestedEvent via event bus
    - Triggered by IntelligenceEngineScheduler (bot) or API endpoint

Wave 2 (next):
    - IntelligenceEngineListener (ai segment) subscribes to IntelligenceEngineRequestedEvent
    - Runs AI verdict synthesis → emits IntelligenceEngineCompletedEvent

Wave 3:
    - FeedbackStore ingests EngineFeedbackSubmittedEvent
    - Signal weights adjusted based on outcome history

Wave 4:
    - SelfImprovementAdvisor runs weekly, emits EvolutionSuggestionReadyEvent
    - Owner reviews suggestions before any code change
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

from src.platform.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# SystemSnapshot — cross-segment state at a point in time
# ---------------------------------------------------------------------------

@dataclass
class SystemSnapshot:
    """Aggregated view of system state across all segments.

    Wave 1: populated with lightweight counts — no heavy AI queries.
    Wave 2+: enriched with signal scores, thesis health, portfolio risk.

    Fields are additive — unset fields default to safe zero values.
    Callers should never raise on missing data; degrade gracefully.
    """
    user_id: str
    phase: str                          # "morning" | "eod"
    triggered_by: str                   # "scheduler" | "command" | "api"
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(tz=datetime.UTC)
    )

    # watchlist segment
    active_alert_count: int = 0
    triggered_alert_count: int = 0      # alerts fired in last scan cycle

    # thesis segment
    active_thesis_count: int = 0
    stale_thesis_count: int = 0         # not reviewed in > 3 days
    thesis_due_review: list[str] = field(default_factory=list)  # thesis IDs

    # market segment
    market_anomaly_tickers: list[str] = field(default_factory=list)

    # portfolio segment
    open_position_count: int = 0
    high_risk_position_count: int = 0   # positions breaching risk thresholds

    # signal engine (injected by SignalEngineCompletedEvent if available)
    signal_engine_summary: str = ""
    ranked_signal_count: int = 0
    risk_alert_count: int = 0
    opportunity_count: int = 0

    # freeform metadata for downstream consumers
    meta: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# IntelligenceEngine
# ---------------------------------------------------------------------------

class IntelligenceEngine:
    """Core orchestrator — Wave 1 implementation.

    Entry point: run_cycle(user_id, phase, triggered_by)

    Wave 1 flow:
        1. build_snapshot()  — aggregate cross-segment counts from DB
        2. _emit_requested() — publish IntelligenceEngineRequestedEvent
           (AI listener in Wave 2 will subscribe and produce verdict)
        3. Return snapshot for callers that need it synchronously
           (e.g. direct API calls, test harness)

    Graceful degradation:
        - Each snapshot field is populated independently; one segment
          failing never blocks the others.
        - If event bus publish fails, snapshot is still returned.
          Downstream silence is preferable to an exception cascade.
    """

    async def run_cycle(
        self,
        user_id: str,
        phase: str,
        triggered_by: str = "scheduler",
        signal_engine_summary: str = "",
    ) -> SystemSnapshot:
        """Run one intelligence cycle. Returns the built snapshot.

        Args:
            user_id:               Target investor (settings.scheduler_user_id).
            phase:                 "morning" or "eod".
            triggered_by:          Source of trigger ("scheduler", "command", "api").
            signal_engine_summary: Optional summary injected from SignalEngineCompletedEvent.
        """
        snapshot = await self._build_snapshot(
            user_id=user_id,
            phase=phase,
            triggered_by=triggered_by,
            signal_engine_summary=signal_engine_summary,
        )

        await self._emit_requested(snapshot)

        return snapshot

    # ── snapshot assembly ──────────────────────────────────────────────────

    async def _build_snapshot(
        self,
        user_id: str,
        phase: str,
        triggered_by: str,
        signal_engine_summary: str,
    ) -> SystemSnapshot:
        """Aggregate cross-segment state. Each block is isolated — failures are non-fatal."""
        from src.platform.db import AsyncSessionLocal

        snapshot = SystemSnapshot(
            user_id=user_id,
            phase=phase,
            triggered_by=triggered_by,
            signal_engine_summary=signal_engine_summary,
        )

        # ── watchlist ──────────────────────────────────────────────────────
        try:
            from src.watchlist.alert_service import AlertService

            async with AsyncSessionLocal() as session:
                alert_svc = AlertService(session)
                alerts = await alert_svc.get_active_alerts(user_id)
                snapshot.active_alert_count = len(alerts)
                snapshot.triggered_alert_count = sum(
                    1 for a in alerts if getattr(a, "is_triggered", False)
                )
        except Exception as exc:
            logger.warning("core.engine.snapshot.watchlist_failed", error=str(exc))

        # ── thesis ────────────────────────────────────────────────────────
        try:
            from src.thesis.thesis_service import ThesisService

            async with AsyncSessionLocal() as session:
                thesis_svc = ThesisService(session)
                theses = await thesis_svc.get_active_theses(user_id)
                snapshot.active_thesis_count = len(theses)
                stale_cutoff = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=3)
                stale = [
                    str(t.id) for t in theses
                    if getattr(t, "last_reviewed_at", None)
                    and t.last_reviewed_at < stale_cutoff
                ]
                snapshot.stale_thesis_count = len(stale)
                snapshot.thesis_due_review = stale
        except Exception as exc:
            logger.warning("core.engine.snapshot.thesis_failed", error=str(exc))

        # ── portfolio ─────────────────────────────────────────────────────
        try:
            from src.portfolio.portfolio_service import PortfolioService

            async with AsyncSessionLocal() as session:
                portfolio_svc = PortfolioService(session)
                positions = await portfolio_svc.get_open_positions(user_id)
                snapshot.open_position_count = len(positions)
                snapshot.high_risk_position_count = sum(
                    1 for p in positions if getattr(p, "is_high_risk", False)
                )
        except Exception as exc:
            logger.warning("core.engine.snapshot.portfolio_failed", error=str(exc))

        logger.info(
            "core.engine.snapshot_built",
            user_id=user_id,
            phase=phase,
            active_alerts=snapshot.active_alert_count,
            active_theses=snapshot.active_thesis_count,
            stale_theses=snapshot.stale_thesis_count,
            open_positions=snapshot.open_position_count,
            high_risk_positions=snapshot.high_risk_position_count,
            signal_engine_summary_len=len(signal_engine_summary),
        )

        return snapshot

    # ── event emit ─────────────────────────────────────────────────────────

    async def _emit_requested(
        self,
        snapshot: SystemSnapshot,
    ) -> None:
        """Publish IntelligenceEngineRequestedEvent with snapshot context.

        Wave 2: IntelligenceEngineListener (ai segment) subscribes here
        and produces IntelligenceEngineCompletedEvent with AI verdict.
        """
        try:
            from src.platform.event_bus import get_event_bus
            from src.platform.events import IntelligenceEngineRequestedEvent

            context_hint = (
                f"phase={snapshot.phase} "
                f"alerts={snapshot.active_alert_count} "
                f"stale_theses={snapshot.stale_thesis_count} "
                f"high_risk_positions={snapshot.high_risk_position_count}"
            )

            bus = get_event_bus()
            await bus.publish(
                IntelligenceEngineRequestedEvent(
                    trigger_type="scheduled",
                    trigger_source=snapshot.triggered_by,
                    user_id=snapshot.user_id,
                    priority="high" if snapshot.high_risk_position_count > 0 else "normal",
                    context_hint=context_hint,
                    signal_engine_summary=snapshot.signal_engine_summary,
                )
            )
            logger.info(
                "core.engine.event_emitted",
                phase=snapshot.phase,
                priority="high" if snapshot.high_risk_position_count > 0 else "normal",
            )
        except Exception as exc:
            logger.error("core.engine.emit_failed", error=str(exc))
            # Non-fatal: snapshot was already built and logged


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_engine: IntelligenceEngine | None = None


def get_intelligence_engine() -> IntelligenceEngine:
    """Return the module-level IntelligenceEngine singleton."""
    global _engine
    if _engine is None:
        _engine = IntelligenceEngine()
    return _engine
