"""
IntelligenceVerdictAgent — AI synthesis for the Core Intelligence Engine.

Owner: ai segment.
Called by: core.engine via thin interface — no prompt/AI logic in core.

Pattern: AISpec + structured_call(), same as all other agents in this module.

Input:  SystemSnapshot + list[RankedSignal]  (from src.core.schemas)
Output: VerdictOutput (from src.ai.schemas)

Fallback contract:
    Any exception from AIClient is caught here.
    Returns _FALLBACK (verdict=NO_ACTION, confidence=0.0) so engine can
    fall through to Wave 1 heuristic without crashing.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.ai.client import AIClient
from src.ai.schemas import VerdictOutput  # canonical location — no circular import
from src.ai.prompts.intelligence_verdict import SPEC, build_user_prompt
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from src.core.schemas import RankedSignal, SystemSnapshot

# Re-export for any external code that does:
#   from src.ai.agents.intelligence_verdict import VerdictOutput
__all__ = ["IntelligenceVerdictAgent", "VerdictOutput"]

logger = get_logger(__name__)


_FALLBACK = VerdictOutput(
    verdict="NO_ACTION",
    confidence=0.0,
    risk_signals=[],
    next_watch_items=[],
    action="AI verdict unavailable — heuristic fallback active",
    reasoning_summary="AI call failed or timed out",
)


class IntelligenceVerdictAgent:
    """Synthesise a structured verdict from SystemSnapshot + ranked signals.

    Usage::

        agent = IntelligenceVerdictAgent(ai_client)
        output = await agent.run(snapshot, ranked_signals)
        # output.verdict, output.confidence, output.action ...
    """

    def __init__(self, ai_client: AIClient) -> None:
        self._client = ai_client

    async def run(
        self,
        snapshot: "SystemSnapshot",
        ranked_signals: "list[RankedSignal]",
    ) -> VerdictOutput:
        user_prompt = build_user_prompt(snapshot, ranked_signals)

        try:
            result: VerdictOutput = await self._client.structured_call(
                spec=SPEC,
                user_prompt=user_prompt,
            )
            logger.info(
                "intelligence_verdict_agent.success",
                verdict=result.verdict,
                confidence=result.confidence,
            )
            return result

        except Exception as exc:
            logger.warning(
                "intelligence_verdict_agent.failed",
                error=str(exc),
            )
            return _FALLBACK
