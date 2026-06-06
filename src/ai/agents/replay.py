"""ReplayAgent — analyze past investor decisions and generate learning feedback.

Owner: ai segment.
Consumed by: thesis.decision_service.DecisionService

Boundary:
- Accepts ReplayContext only.
- Returns DecisionReplayResult only.
- Does not write DB, does not load repository, does not dispatch notifications.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from src.ai.client import AIClient
from src.ai.prompts.replay import ReplayContext, SYSTEM_PROMPT, build_user_prompt
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# Confidence label → float for episodic memory storage
_CONFIDENCE_MAP = {"HIGH": 0.85, "MEDIUM": 0.65, "LOW": 0.45}


class DecisionReplayResult(BaseModel):
    decision_id: int
    ticker: str
    decision_type: str = Field(..., description="BUY | SELL | HOLD | ADD | REDUCE")
    outcome_verdict: str = Field(..., description="CORRECT | INCORRECT | MIXED")
    what_went_right: list[str] = Field(default_factory=list)
    what_went_wrong: list[str] = Field(default_factory=list)
    key_lesson: str
    pattern_detected: str | None = None
    suggested_adjustment: str | None = None
    confidence: str = Field(..., description="HIGH | MEDIUM | LOW")


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
            raw = await self._client.call(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_user_prompt(ctx),
                response_schema=DecisionReplayResult,
                temperature=0.2,
            )
            result = raw  # client.call() already returns parsed Pydantic model
            logger.info(
                "decision_replay.analyzed",
                decision_id=result.decision_id,
                ticker=result.ticker,
                verdict=result.outcome_verdict,
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

        # key_points: key_lesson + pattern_detected (if any)
        key_lines = [result.key_lesson]
        if result.pattern_detected:
            key_lines.append(f"pattern: {result.pattern_detected}")
        if result.suggested_adjustment:
            key_lines.append(f"adjust: {result.suggested_adjustment}")

        # risk_signals: what went wrong
        risk_lines = [str(w) for w in (result.what_went_wrong or [])[:5]]

        entry = InteractionEntry(
            user_id=user_id,
            agent_type="replay",
            trigger=trigger,
            tickers=[result.ticker],
            ai_verdict=result.outcome_verdict,
            ai_confidence=_CONFIDENCE_MAP.get(result.confidence.upper(), 0.65),
            ai_key_points="\n".join(key_lines) if key_lines else None,
            ai_risk_signals="\n".join(risk_lines) if risk_lines else None,
            decision_id=result.decision_id,
        )
        await MemoryService.log_interaction(session, entry)
    except Exception as exc:
        logger.warning("replay_agent.memory_log_failed", error=str(exc))
