"""BriefingAgent — AI agent for morning and EOD brief generation.

Owner: ai segment.
Callers: briefing.BriefingService.

This agent is a thin wrapper around the AI client. It:
- builds prompts via ai/prompts/brief.py
- calls the AI client with the appropriate schema
- logs interaction to ai.memory (Layer 2) after every call
- returns structured BriefOutput

No business logic, no DB access, no Discord formatting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.ai.client import AIClient
from src.ai.prompts.brief import (
    SYSTEM_PROMPT,
    build_morning_prompt,
    build_eod_prompt,
)
from src.ai.schemas import BriefOutput
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class BriefingAgent:
    """Generates morning and EOD briefs via AI.

    Args:
        client: Injected AIClient with retry/circuit-breaker.
    """

    def __init__(self, client: AIClient) -> None:
        self._client = client

    async def morning_brief(
        self,
        market_context: str,
        watchlist_tickers: list[str],
        extra_context: str = "",
        portfolio_context: str = "",
        thesis_context: str = "",
        past_lessons: str = "",
        investor_profile: str = "",
        feedback_summary: str = "",
        # Memory wiring params (optional, backward-compat)
        session: AsyncSession | None = None,
        user_id: str | None = None,
        trigger: str = "morning_brief",
    ) -> BriefOutput:
        """Generate a morning brief for the given watchlist and market context.

        Args:
            market_context:    Market data string (quotes, indices, news summary).
            watchlist_tickers: List of ticker symbols in the user's watchlist.
            extra_context:     Optional free-form additional context.
            portfolio_context: Optional portfolio P&L snapshot string.
            thesis_context:    Optional active thesis summary string.
            past_lessons:      Optional formatted lesson history from LessonService.
            investor_profile:  Optional pre-rendered investor profile block from
                               ContextBuilder.render_for_agent(). When provided,
                               the AI personalises prioritized_actions against the
                               investor's risk appetite, avoid list, and patterns.
            feedback_summary:  Optional feedback calibration string from
                               BriefingService._build_feedback_context(). When
                               provided, the AI adjusts action count and specificity
                               based on the user's historical acted_rate. Does not
                               override risk_appetite from investor_profile.
            session:           Optional AsyncSession for memory logging.
            user_id:           Optional user_id for memory logging.
            trigger:           Trigger label for episodic log (default: morning_brief).
        """
        prompt = build_morning_prompt(
            market_context=market_context,
            watchlist_tickers=watchlist_tickers,
            extra_context=extra_context,
            portfolio_context=portfolio_context,
            thesis_context=thesis_context,
            past_lessons=past_lessons,
            investor_profile=investor_profile,
            feedback_summary=feedback_summary,
        )
        logger.debug(
            "briefing_agent.morning_brief.calling_ai",
            ticker_count=len(watchlist_tickers),
            has_portfolio=bool(portfolio_context),
            has_thesis=bool(thesis_context),
            has_lessons=bool(past_lessons),
            has_investor_profile=bool(investor_profile),
            has_feedback_summary=bool(feedback_summary),
        )
        result: BriefOutput = await self._client.chat(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            response_schema=BriefOutput,
            max_tokens=AIClient.COMPLEX_MAX_TOKENS,
        )
        logger.info(
            "briefing_agent.morning_brief.done",
            sentiment=getattr(result, "sentiment", None),
            action_count=len(getattr(result, "prioritized_actions", []) or []),
        )

        # --- Memory: log interaction (Layer 2) ---
        await _log_brief_interaction(
            session=session,
            user_id=user_id,
            result=result,
            tickers=watchlist_tickers,
            trigger=trigger,
            agent_type="briefing",
        )

        return result

    async def eod_brief(
        self,
        market_context: str,
        watchlist_tickers: list[str],
        extra_context: str = "",
        portfolio_context: str = "",
        thesis_context: str = "",
        past_lessons: str = "",
        investor_profile: str = "",
        feedback_summary: str = "",
        # Memory wiring params (optional, backward-compat)
        session: AsyncSession | None = None,
        user_id: str | None = None,
    ) -> BriefOutput:
        """Generate an end-of-day brief.

        EOD brief injects the same context blocks as morning_brief so the
        AI can review portfolio alignment, detect thesis stop_loss breaches
        for the next session, and personalise the recap via investor profile
        and past lessons.

        Args:
            market_context:    EOD market data string (closing quotes, session recap).
            watchlist_tickers: List of ticker symbols in the user's watchlist.
            extra_context:     Optional free-form additional context.
            portfolio_context: Optional portfolio P&L snapshot string.
            thesis_context:    Optional active thesis summary string.
            past_lessons:      Optional formatted lesson history from LessonService.
            investor_profile:  Optional pre-rendered investor profile block.
            feedback_summary:  Optional feedback calibration string. Same semantics
                               as morning_brief — adjusts action specificity only,
                               never overrides investor_profile constraints.
            session:           Optional AsyncSession for memory logging.
            user_id:           Optional user_id for memory logging.
        """
        prompt = build_eod_prompt(
            market_context=market_context,
            watchlist_tickers=watchlist_tickers,
            extra_context=extra_context,
            portfolio_context=portfolio_context,
            thesis_context=thesis_context,
            past_lessons=past_lessons,
            investor_profile=investor_profile,
            feedback_summary=feedback_summary,
        )
        logger.debug(
            "briefing_agent.eod_brief.calling_ai",
            ticker_count=len(watchlist_tickers),
            has_portfolio=bool(portfolio_context),
            has_thesis=bool(thesis_context),
            has_lessons=bool(past_lessons),
            has_investor_profile=bool(investor_profile),
            has_feedback_summary=bool(feedback_summary),
        )
        result: BriefOutput = await self._client.chat(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            response_schema=BriefOutput,
            max_tokens=AIClient.COMPLEX_MAX_TOKENS,
        )
        logger.info(
            "briefing_agent.eod_brief.done",
            sentiment=getattr(result, "sentiment", None),
            action_count=len(getattr(result, "prioritized_actions", []) or []),
        )

        # --- Memory: log interaction (Layer 2) ---
        await _log_brief_interaction(
            session=session,
            user_id=user_id,
            result=result,
            tickers=watchlist_tickers,
            trigger="eod_brief",
            agent_type="briefing",
        )

        return result


# ---------------------------------------------------------------------------
# Internal helper — keeps agent methods readable
# ---------------------------------------------------------------------------

async def _log_brief_interaction(
    session,
    user_id: str | None,
    result: BriefOutput,
    tickers: list[str],
    trigger: str,
    agent_type: str,
) -> None:
    """Fire-and-forget memory log. Never raises."""
    if session is None or not user_id:
        return
    try:
        from src.ai.memory.memory_service import InteractionEntry, MemoryService

        key_points_lines = []
        for action in (getattr(result, "prioritized_actions", []) or [])[:5]:
            if isinstance(action, str):
                key_points_lines.append(action)
            elif hasattr(action, "action"):
                key_points_lines.append(str(action.action))

        entry = InteractionEntry(
            user_id=user_id,
            agent_type=agent_type,
            trigger=trigger,
            tickers=tickers[:10],
            ai_verdict=str(getattr(result, "sentiment", "") or ""),
            ai_key_points="\n".join(key_points_lines) if key_points_lines else None,
        )
        await MemoryService.log_interaction(session, entry)
    except Exception as exc:
        logger.warning("briefing_agent.memory_log_failed", error=str(exc))
