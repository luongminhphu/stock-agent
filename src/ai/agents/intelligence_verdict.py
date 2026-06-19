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

from typing import TYPE_CHECKING, Any

from src.ai.client import AIClient
from src.ai.prompt_cache import PromptCache
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
    conviction="low",
    time_horizon="intraday",
    thesis_alignment=0.5,
    key_risk="Không thể đánh giá rủi ro — AI call thất bại hoặc timeout",
    invalidation_trigger="Verdict này sai khi AI khôi phục và trả về kết quả khác",
    action="AI verdict unavailable — heuristic fallback active",
    reasoning_summary="AI call failed or timed out",
    confidence=0.0,
    risk_signals=[],
    next_watch_items=[],
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
        session: Any = None,
        user_id: str | None = None,
        **_: Any,
    ) -> VerdictOutput:
        investor_context = await _fetch_investor_context(session, user_id)
        user_prompt = build_user_prompt(snapshot, ranked_signals, investor_context=investor_context)

        # Cache guard: skip AI if same prompt seen within TTL
        cached = _verdict_cache.get(SPEC.system_prompt, user_prompt, VerdictOutput)
        if cached is not None:
            return cached

        try:
            result: VerdictOutput = await self._client.structured_call(
                spec=SPEC,
                user_prompt=user_prompt,
            )
            _verdict_cache.set(SPEC.system_prompt, user_prompt, result)
            logger.info(
                "intelligence_verdict_agent.success",
                verdict=result.verdict,
                confidence=result.confidence,
                cache_stats=_verdict_cache.stats,
            )

            # Log to episodic memory (fire-and-forget)
            if session and user_id:
                try:
                    from src.ai.memory.memory_service import InteractionEntry, MemoryService

                    entry = InteractionEntry(
                        user_id=user_id,
                        agent_type="intelligence_verdict",
                        trigger="engine_cycle",
                        tickers=[],
                        ai_verdict=str(result.verdict),
                        ai_confidence=result.confidence,
                        ai_key_points=result.reasoning_summary[:200] if result.reasoning_summary else None,
                    )
                    await MemoryService.log_interaction(session, entry)
                except Exception as log_exc:
                    logger.warning(
                        "intelligence_verdict_agent.log_interaction_failed",
                        error=str(log_exc),
                    )

            return result

        except Exception as exc:
            logger.warning(
                "intelligence_verdict_agent.failed",
                error=str(exc),
            )
            return _FALLBACK

async def _fetch_investor_context(session: Any, user_id: str | None) -> str:
    """Fetch and render investor memory context for prompt injection.

    Returns empty string on any failure -- never raises.
    """
    if not session or not user_id:
        return ""
    try:
        from src.ai.context_builder import ContextBuilder, render_for_agent

        ctx = await ContextBuilder(session).build(user_id=user_id)
        return render_for_agent(ctx)
    except Exception as exc:
        logger.warning("intelligence_verdict_agent.investor_context_failed", error=str(exc))
        return ""
