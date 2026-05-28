"""IntelligenceEngine — orchestration core.

Owner: core segment.

Wave 1: signals-based synthesis (rule-based, no AI call).
Wave 2: replace _synthesize() with AIClient.generate_verdict(signals).
Wave 3: dispatch is event-based via _EngineRunner.run_cycle() publishing
        IntelligenceEngineCompletedEvent, consumed by IntelligenceEngineListener
        for Discord delivery. _dispatch() is kept minimal and should not be
        extended with direct bot/briefing calls — new integrations must listen
        on the completed event instead.

Design principles:
- run_cycle() is the single entry point — snapshot → signals → synthesize → dispatch.
- Each step is replaceable without touching the others.
- All errors are caught per-step; partial output is always returned.

Module-level API (used by IntelligenceEngineScheduler):
- get_intelligence_engine(): returns a _EngineRunner singleton.
- run_cycle(user_id, phase, ...): opens its own session, runs full cycle,
  publishes IntelligenceEngineCompletedEvent, returns EngineVerdict | None.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.schemas import EngineOutput, EngineVerdict, RankedSignal, SystemSnapshot, VerdictType
from src.core.signals import rank_signals
from src.core.snapshot import SystemSnapshotBuilder
from src.platform.logging import get_logger

logger = get_logger(__name__)


class IntelligenceEngine:
    """Central AI orchestrator for one investor.

    Usage::

        engine = IntelligenceEngine(session, user_id)
        output = await engine.run_cycle()
    """

    DISPATCH_THRESHOLD = 0.65  # only dispatch if confidence >= this

    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def run_cycle(
        self,
        trigger_source: str = "",
        signal_engine_summary: str | None = None,
    ) -> EngineOutput:
        """Full cycle: build snapshot → rank signals → synthesize verdict → dispatch.

        Args:
            trigger_source: caller identity forwarded into the snapshot
                (e.g. "scheduler", "discord_command", "manual").
            signal_engine_summary: free-text summary from upstream callers
                attached to the snapshot for the AI prompt.

        Note: external integrations (Discord, briefing, APIs) SHOULD NOT
        hook into _dispatch() directly. They must subscribe to
        IntelligenceEngineCompletedEvent, which is published by the
        module-level _EngineRunner.run_cycle().
        """
        snapshot = await SystemSnapshotBuilder(
            self.session,
            self.user_id,
            trigger_source=trigger_source,
            signal_engine_summary=signal_engine_summary,
        ).build()
        signals = rank_signals(snapshot)
        verdict = await self._synthesize(snapshot, signals)
        dispatched = await self._dispatch(verdict)
        return EngineOutput(
            snapshot=snapshot,
            verdict=verdict,
            dispatched_to=dispatched,
        )

    # ------------------------------------------------------------------
    # Wave 1: signals-based rule synthesis
    # Wave 2: replace body with AIClient.generate_verdict(signals)
    # ------------------------------------------------------------------

    async def _synthesize(
        self,
        snap: SystemSnapshot,
        signals: list[RankedSignal],
    ) -> EngineVerdict:
        """Derive verdict from ranked signals using priority rules."""
        if not signals:
            return self._no_action_verdict(snap)

        top = signals[0]
        verdict_type, confidence = self._map_signal_to_verdict(top)

        risk_signals = [
            s.description for s in signals if s.source in ("portfolio", "watchlist")
        ][:5]
        next_watch = [
            s.description for s in signals if s.source in ("thesis", "market")
        ][:5]
        sources = list({s.source for s in signals})

        summary = " | ".join(
            f"{s.source}:{s.urgency_score:.2f}" for s in signals[:4]
        )

        return EngineVerdict(
            verdict_id=str(uuid.uuid4()),
            verdict=verdict_type,
            confidence=confidence,
            risk_signals=risk_signals,
            next_watch_items=next_watch,
            action=self._derive_action(verdict_type, snap),
            reasoning_summary=summary,
            sources=sources,
            generated_at=datetime.now(timezone.utc),
        )

    def _map_signal_to_verdict(
        self, top: RankedSignal
    ) -> tuple[VerdictType, float]:
        """Map the highest-scored signal to a verdict + confidence."""
        if top.source == "portfolio":
            return "RISK_ALERT", min(0.95, 0.70 + top.urgency_score * 0.25)
        if top.source == "thesis" and "invalidate" in top.description.lower():
            return "REVIEW_THESIS", min(0.90, 0.65 + top.urgency_score * 0.25)
        if top.source == "watchlist":
            return "RISK_ALERT", min(0.85, 0.60 + top.urgency_score * 0.25)
        if top.source == "thesis":
            return "REVIEW_THESIS", min(0.80, 0.55 + top.urgency_score * 0.25)
        if top.source == "market" and top.urgency_score >= 0.4:
            return "HOLD", min(0.75, 0.50 + top.urgency_score * 0.25)
        return "NO_ACTION", 0.40

    def _no_action_verdict(self, snap: SystemSnapshot) -> EngineVerdict:
        return EngineVerdict(
            verdict_id=str(uuid.uuid4()),
            verdict="NO_ACTION",
            confidence=0.40,
            risk_signals=[],
            next_watch_items=[],
            action="Không có action ưu tiên. Hệ thống ổn định.",
            reasoning_summary="0 signals detected across all segments",
            sources=[],
            generated_at=datetime.now(timezone.utc),
        )

    def _derive_action(self, verdict: VerdictType, snap: SystemSnapshot) -> str:
        if verdict == "RISK_ALERT":
            tickers = ", ".join(
                a.ticker for a in snap.watchlist_alerts[:3]
            ) or ", ".join(snap.portfolio.top_exposed_tickers[:3])
            return f"Kiểm tra ngay: {tickers}" if tickers else "Kiểm tra risk breach"
        if verdict == "REVIEW_THESIS":
            tickers = ", ".join(t.ticker for t in snap.thesis_due_review[:3])
            return f"Review thesis: {tickers}" if tickers else "Review thesis overdue"
        if verdict == "HOLD":
            tickers = ", ".join(s.ticker for s in snap.market_anomalies[:3])
            return f"Theo dõi tín hiệu thị trường: {tickers}" if tickers else "Theo dõi thị trường"
        return "Không có action ưu tiên. Hệ thống ổn định."

    # ------------------------------------------------------------------
    # Wave 1: dispatch only records local routing decisions.
    #
    # External side-effects (Discord, briefing, API notifications) are
    # handled by listeners of IntelligenceEngineCompletedEvent emitted
    # by the module-level _EngineRunner. Do NOT add cross-segment calls
    # here — that would break core's read-only contract.
    # ------------------------------------------------------------------

    async def _dispatch(self, verdict: EngineVerdict) -> list[str]:
        """Record internal dispatch decision for observability.

        Currently returns ["log"] when confidence passes the threshold.
        Kept for backward compatibility with EngineOutput.dispatched_to
        but does not perform any external side-effects.
        """
        dispatched: list[str] = []
        if verdict.confidence >= self.DISPATCH_THRESHOLD:
            dispatched.append("log")
        return dispatched


# ---------------------------------------------------------------------------
# Module-level API — used by IntelligenceEngineScheduler (bot segment)
# ---------------------------------------------------------------------------

class _EngineRunner:
    """Stateless runner — holds no session, safe as a module singleton."""

    async def run_cycle(
        self,
        user_id: str,
        phase: str = "morning",
        triggered_by: str = "scheduler",
        signal_engine_summary: str = "",
        verdict_agent: Any | None = None,
        context_hint: str | None = None,
        trigger_source: str = "",
        priority: str = "normal",
    ) -> EngineVerdict | None:
        from src.platform.db import AsyncSessionLocal

        # Resolve effective trigger_source: prefer explicit arg, fall back to triggered_by
        effective_trigger = trigger_source or triggered_by

        logger.info(
            "engine.run_cycle.start",
            user_id=user_id,
            phase=phase,
            triggered_by=triggered_by,
            trigger_source=effective_trigger,
            has_signal_summary=bool(signal_engine_summary),
            has_verdict_agent=verdict_agent is not None,
        )

        try:
            async with AsyncSessionLocal() as session:
                engine = IntelligenceEngine(session=session, user_id=user_id)
                output = await engine.run_cycle(
                    trigger_source=effective_trigger,
                    signal_engine_summary=signal_engine_summary or None,
                )
            verdict = output.verdict
        except Exception as exc:
            logger.error(
                "engine.run_cycle.snapshot_failed",
                user_id=user_id,
                phase=phase,
                error=str(exc),
            )
            return None

        # Wave 2: override heuristic verdict with AI synthesis when agent present
        if verdict_agent is not None:
            try:
                ai_verdict = await verdict_agent.run(
                    snapshot=output.snapshot,
                    signals_summary=signal_engine_summary or verdict.reasoning_summary,
                    phase=phase,
                )
                if ai_verdict is not None:
                    verdict = ai_verdict
                    logger.info(
                        "engine.run_cycle.ai_verdict_applied",
                        verdict=verdict.verdict,
                        confidence=verdict.confidence,
                    )
            except Exception as exc:
                logger.warning(
                    "engine.run_cycle.ai_verdict_failed",
                    error=str(exc),
                    fallback="using_heuristic_verdict",
                )

        # Extract tickers from snapshot for GlobalRiskStore + downstream consumers
        flagged_tickers = _extract_snapshot_tickers(output.snapshot)

        # Publish completed event
        # Consumers: IntelligenceEngineListener (Discord), GlobalRiskSubscriber (readmodel)
        try:
            from src.platform.event_bus import get_event_bus
            from src.platform.events import IntelligenceEngineCompletedEvent

            completed = IntelligenceEngineCompletedEvent(
                user_id=user_id,
                verdict=verdict.verdict,
                confidence=verdict.confidence,
                action_required=verdict.verdict not in ("NO_ACTION", "HOLD"),
                summary=verdict.action,
                trigger_source=effective_trigger,
                flagged_tickers=flagged_tickers,
            )
            bus = get_event_bus()
            await bus.publish(completed)

            logger.info(
                "engine.run_cycle.completed_event_published",
                user_id=user_id,
                verdict=verdict.verdict,
                confidence=verdict.confidence,
                phase=phase,
                action_required=completed.action_required,
                flagged_ticker_count=len(flagged_tickers),
            )
        except Exception as exc:
            logger.error(
                "engine.run_cycle.event_publish_failed",
                error=str(exc),
                verdict=verdict.verdict,
            )

        return verdict


def _extract_snapshot_tickers(snapshot: SystemSnapshot) -> tuple[str, ...]:
    """Extract all flagged tickers from a SystemSnapshot.

    Sources (in priority order):
    1. watchlist_alerts — tickers with active triggered alerts
    2. thesis_due_review — tickers with stale/overdue thesis
    3. portfolio.top_exposed_tickers — high-exposure positions
    4. watchlist.top_tickers — top watchlist signals

    Deduplicates and uppercases all tickers.
    """
    seen: set[str] = set()
    result: list[str] = []

    def _add(ticker: str) -> None:
        t = ticker.upper().strip()
        if t and t not in seen:
            seen.add(t)
            result.append(t)

    for alert in snapshot.watchlist_alerts:
        _add(alert.ticker)
    for thesis_ref in snapshot.thesis_due_review:
        _add(thesis_ref.ticker)
    for ticker in snapshot.portfolio.top_exposed_tickers:
        _add(ticker)
    for ticker in snapshot.watchlist.top_tickers:
        _add(ticker)

    return tuple(result)


# Module-level singleton
_engine_runner: _EngineRunner | None = None


def get_intelligence_engine() -> _EngineRunner:
    """Return the module-level _EngineRunner singleton."""
    global _engine_runner
    if _engine_runner is None:
        _engine_runner = _EngineRunner()
        logger.info("engine.runner_singleton_created")
    return _engine_runner
