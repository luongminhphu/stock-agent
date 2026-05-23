"""Intelligence Engine — core orchestrator.

Owner: core segment.

Wave 1: Build SystemSnapshot + emit IntelligenceEngineRequestedEvent.
Wave 2 (this file): Accept verdict_agent, delegate to snapshot.py + signals.py,
    map VerdictOutput → EngineVerdict, apply confidence gate.
Wave 3: FeedbackStore ingests EngineFeedbackSubmittedEvent.
Wave 4: SelfImprovementAdvisor runs weekly, emits EvolutionSuggestionReadyEvent.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core import snapshot as snapshot_module
from src.core import signals as signals_module
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from src.ai.agents.intelligence_verdict import IntelligenceVerdictAgent
    from src.core.schemas import EngineVerdict

logger = get_logger(__name__)

_CONFIDENCE_THRESHOLD = 0.65


class IntelligenceEngine:
    """Core orchestrator — Wave 2 implementation.

    Entry point: run_cycle(user_id, trigger_source, ...)

    Flow:
        1. snapshot.build_snapshot()  — parallel cross-segment DB queries
        2. signals.rank_signals()     — deterministic urgency scoring
        3. verdict_agent.run()        — AI synthesis (Wave 2, optional)
           OR _heuristic_verdict()   — fallback when no agent provided
        4. confidence gate            — drop low-confidence verdicts
           (bypass for RISK_ALERT)

    Graceful degradation:
        - Each snapshot field populated independently; failures non-fatal.
        - If verdict_agent raises, falls through to heuristic.
        - If ranked signals empty, returns None (no action).
    """

    async def run_cycle(
        self,
        user_id: str,
        trigger_source: str = "scheduler",
        priority: str = "normal",
        context_hint: str | None = None,
        signal_engine_summary: str = "",
        verdict_agent: "IntelligenceVerdictAgent | None" = None,
    ) -> "EngineVerdict | None":
        """Run one intelligence cycle. Returns EngineVerdict or None.

        Args:
            user_id:               Target investor.
            trigger_source:        Source of trigger ("scheduler", "command", "api").
            priority:              "normal" | "high".
            context_hint:          Optional free-text hint for AI prompt.
            signal_engine_summary: Injected from SignalEngineCompletedEvent.
            verdict_agent:         IntelligenceVerdictAgent (Wave 2 AI active).
                                   When None, heuristic fallback runs only.
        """
        from src.core.schemas import EngineVerdict  # noqa: F401 (TYPE_CHECKING workaround)

        # Step 1: build cross-segment snapshot
        snap = await snapshot_module.build_snapshot(
            user_id=user_id,
            trigger_source=trigger_source,
            signal_engine_summary=signal_engine_summary,
        )

        # Step 2: rank signals — deterministic, no AI
        ranked = signals_module.rank_signals(snap)

        if not ranked:
            logger.info(
                "core.engine.no_signals",
                trigger_source=trigger_source,
                user_id=user_id,
            )
            return None

        # Step 3: produce verdict
        if verdict_agent is not None:
            verdict = await self._ai_verdict(
                verdict_agent=verdict_agent,
                snap=snap,
                ranked=ranked,
                trigger_source=trigger_source,
            )
        else:
            verdict = self._heuristic_verdict(
                snap=snap,
                ranked=ranked,
                trigger_source=trigger_source,
            )

        # Step 4: confidence gate (bypass for RISK_ALERT — always surface)
        if verdict.confidence < _CONFIDENCE_THRESHOLD and verdict.verdict != "RISK_ALERT":
            logger.info(
                "core.engine.below_threshold",
                verdict=verdict.verdict,
                confidence=verdict.confidence,
                trigger_source=trigger_source,
            )
            return None

        logger.info(
            "core.engine.verdict_ready",
            verdict=verdict.verdict,
            confidence=verdict.confidence,
            signal_count=len(ranked),
            trigger_source=trigger_source,
        )
        return verdict

    async def _ai_verdict(
        self,
        verdict_agent: Any,
        snap: Any,
        ranked: list,
        trigger_source: str,
    ) -> "EngineVerdict":
        """Call IntelligenceVerdictAgent and map VerdictOutput → EngineVerdict."""
        from src.core.schemas import EngineVerdict

        try:
            output = await verdict_agent.run(
                snapshot=snap,
                ranked_signals=ranked,
            )
            return EngineVerdict(
                verdict=output.verdict,
                confidence=output.confidence,
                risk_signals=output.risk_signals,
                next_watch_items=output.next_watch_items,
                action=output.action,
                reasoning_summary=output.reasoning_summary,
                top_signals=ranked,
                trigger_source=trigger_source,
            )
        except Exception as exc:
            logger.error("core.engine.ai_verdict_failed", error=str(exc))
            return self._heuristic_verdict(
                snap=snap,
                ranked=ranked,
                trigger_source=f"{trigger_source}:ai_fallback",
            )

    def _heuristic_verdict(self, snap: Any, ranked: list, trigger_source: str) -> "EngineVerdict":
        """Wave 1 heuristic — used when no AI agent or AI fails."""
        from src.core.schemas import EngineVerdict

        _verdict_map = {
            "portfolio": "RISK_ALERT",
            "thesis":    "REVIEW_THESIS",
            "watchlist": "WATCH",
            "market":    "WATCH",
        }
        top = ranked[0]
        return EngineVerdict(
            verdict=_verdict_map.get(top.source, "NO_ACTION"),
            confidence=top.urgency_score,
            risk_signals=[
                s.description for s in ranked if s.source == "portfolio"
            ],
            next_watch_items=[s.description for s in ranked[:3]],
            action=top.description,
            reasoning_summary=(
                f"Heuristic: top signal={top.source} "
                f"score={top.urgency_score:.2f}"
            ),
            top_signals=ranked,
            trigger_source=trigger_source,
        )


# ---------------------------------------------------------------------------
# Module-level singleton + convenience alias
# ---------------------------------------------------------------------------

_engine: IntelligenceEngine | None = None


def get_intelligence_engine() -> IntelligenceEngine:
    """Return the module-level IntelligenceEngine singleton."""
    global _engine
    if _engine is None:
        _engine = IntelligenceEngine()
    return _engine


async def run_cycle(**kwargs) -> "EngineVerdict | None":
    """Module-level convenience alias used by intelligence_listener.py."""
    return await get_intelligence_engine().run_cycle(**kwargs)
