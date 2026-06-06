"""ReplayAgent — analyze past investor decisions and generate learning feedback.

Owner: ai segment.
Consumed by: thesis.decision_service.DecisionService

Boundary:
- Accepts ReplayContext only.
- Returns DecisionReplayResult only.
- Does not write DB, does not load repository, does not dispatch notifications.

Schema note:
    AI output is parsed into ReplayOutput (src/ai/schemas/replay.py) which is
    the canonical schema shared by both replay agents.
    DecisionReplayResult is a lightweight adapter that maps ReplayOutput fields
    to the names expected by all downstream callers:
        key_lesson        ← lessons[0]  (first actionable lesson)
        pattern_detected  ← pattern_tag (str value or None)
        suggested_adjustment ← None (not in ReplayOutput; kept for compat)
        confidence        ← float 0–1  (ReplayOutput already uses float)
        outcome_verdict   ← outcome_verdict (str)
    All other fields pass through directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.ai.client import AIClient
from src.ai.prompts.replay import ReplayContext, SYSTEM_PROMPT, build_user_prompt
from src.ai.schemas.replay import ReplayOutput
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class DecisionReplayResult:
    """Adapter: wraps ReplayOutput and exposes the field names used by all
    downstream callers (decision_service, decision_replay_scheduler, embeds,
    api/routes/decisions, scheduler_trigger, investor_profile).

    Fields:
        ticker            str
        decision_type     str   — from ReplayOutput.original_action
        outcome_verdict   str   — WIN | LOSS | BREAK_EVEN | PENDING
        what_went_right   list[str]
        what_went_wrong   list[str]
        key_lesson        str | None   — lessons[0] or None
        pattern_detected  str | None   — pattern_tag.value or None
        suggested_adjustment str | None — not in ReplayOutput; always None
        confidence        float         — 0.0–1.0
        decision_id       int           — injected from ReplayContext
        summary           str
        lessons           list[str]     — full lessons list (passthrough)
    """

    def __init__(self, output: ReplayOutput, decision_id: int) -> None:
        self._output = output
        self.decision_id = decision_id
        self.ticker = output.ticker
        self.decision_type = output.original_action
        self.outcome_verdict = str(output.outcome_verdict)
        self.what_went_right = output.what_went_right
        self.what_went_wrong = output.what_went_wrong
        self.lessons = output.lessons
        self.key_lesson = output.lessons[0] if output.lessons else None
        self.pattern_detected = (
            output.pattern_tag.value if output.pattern_tag else None
        )
        self.suggested_adjustment: str | None = None   # not in ReplayOutput schema
        self.confidence: float = output.confidence     # already float 0–1
        self.summary = output.summary
        self.thesis_accuracy_note = output.thesis_accuracy_note
        self.exit_reason_assessment = output.exit_reason_assessment


class ReplayAgent:
    def __init__(self, ai_client: AIClient) -> None:
        self._client = ai_client

    async def analyze(
        self,
        ctx: ReplayContext,
        # Memory wiring params (optional, backward-compat)
        session: AsyncSession | None = None,
        user_id: str | None = None,
        trigger: str = "decision_replay",
    ) -> DecisionReplayResult | None:
        """Analyze a past decision and return learning feedback.

        Args:
            ctx:      ReplayContext built by DecisionService.
            session:  Optional AsyncSession for memory logging.
            user_id:  Optional user_id for episodic log.
            trigger:  Trigger label (default: decision_replay).
        """
        try:
            output: ReplayOutput = await self._client.chat(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_user_prompt(ctx),
                response_schema=ReplayOutput,
                temperature=0.2,
            )
            result = DecisionReplayResult(output=output, decision_id=ctx.decision_id)
            logger.info(
                "decision_replay.analyzed",
                decision_id=result.decision_id,
                ticker=result.ticker,
                verdict=result.outcome_verdict,
                pattern=result.pattern_detected,
                confidence=result.confidence,
            )
        except Exception as exc:
            logger.warning(
                "decision_replay.analysis_failed",
                decision_id=ctx.decision_id,
                ticker=ctx.ticker,
                error=str(exc),
            )
            return None

        # --- Memory: log interaction (Layer 2) ---
        await _log_replay_interaction(
            session=session,
            user_id=user_id,
            result=result,
            trigger=trigger,
        )

        return result


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

async def _log_replay_interaction(
    session,
    user_id: str | None,
    result: DecisionReplayResult,
    trigger: str,
) -> None:
    """Fire-and-forget memory log. Never raises."""
    if session is None or not user_id:
        return
    try:
        from src.ai.memory.memory_service import InteractionEntry, MemoryService

        # key_points: key_lesson + pattern_detected (if any) + all lessons
        key_lines: list[str] = []
        if result.key_lesson:
            key_lines.append(result.key_lesson)
        if result.pattern_detected:
            key_lines.append(f"pattern: {result.pattern_detected}")
        # Include remaining lessons (beyond the first) for richer memory context
        for lesson in (result.lessons or [])[1:3]:
            key_lines.append(lesson)

        # risk_signals: what went wrong
        risk_lines = [str(w) for w in (result.what_went_wrong or [])[:5]]

        entry = InteractionEntry(
            user_id=user_id,
            agent_type="replay",
            trigger=trigger,
            tickers=[result.ticker],
            ai_verdict=result.outcome_verdict,
            ai_confidence=result.confidence,          # already float
            ai_key_points="\n".join(key_lines) if key_lines else None,
            ai_risk_signals="\n".join(risk_lines) if risk_lines else None,
            decision_id=result.decision_id,
        )
        await MemoryService.log_interaction(session, entry)
    except Exception as exc:
        logger.warning("replay_agent.memory_log_failed", error=str(exc))
