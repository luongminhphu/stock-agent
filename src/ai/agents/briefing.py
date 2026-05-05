"""BriefingAgent — AI agent for morning and EOD brief generation.

Owner: ai segment.
Callers: briefing.BriefingService.

This agent is a thin wrapper around the AI client. It:
- builds prompts via ai/prompts/brief.py
- calls the AI client with the appropriate schema
- returns structured BriefOutput

No business logic, no DB access, no Discord formatting.
"""

from __future__ import annotations

from src.ai.client import AIClient
from src.ai.prompts.brief import (
    SYSTEM_PROMPT,
    build_morning_prompt,
    build_eod_prompt,
)
from src.ai.schemas import BriefOutput
from src.platform.logging import get_logger

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
        """
        prompt = build_morning_prompt(
            market_context=market_context,
            watchlist_tickers=watchlist_tickers,
            extra_context=extra_context,
            portfolio_context=portfolio_context,
            thesis_context=thesis_context,
            past_lessons=past_lessons,
            investor_profile=investor_profile,
        )
        logger.debug(
            "briefing_agent.morning_brief.calling_ai",
            ticker_count=len(watchlist_tickers),
            has_portfolio=bool(portfolio_context),
            has_thesis=bool(thesis_context),
            has_lessons=bool(past_lessons),
            has_investor_profile=bool(investor_profile),
        )
        result: BriefOutput = await self._client.chat(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            response_schema=BriefOutput,
        )
        logger.info(
            "briefing_agent.morning_brief.done",
            sentiment=getattr(result, "sentiment", None),
            action_count=len(getattr(result, "prioritized_actions", []) or []),
        )
        return result

    async def eod_brief(
        self,
        market_context: str,
        watchlist_tickers: list[str],
        extra_context: str = "",
    ) -> BriefOutput:
        """Generate an end-of-day brief.

        EOD brief does not inject investor profile — it is a market recap,
        not a decision-making prompt. Profile injection is morning-only.
        """
        prompt = build_eod_prompt(
            market_context=market_context,
            watchlist_tickers=watchlist_tickers,
            extra_context=extra_context,
        )
        logger.debug(
            "briefing_agent.eod_brief.calling_ai",
            ticker_count=len(watchlist_tickers),
        )
        result: BriefOutput = await self._client.chat(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            response_schema=BriefOutput,
        )
        logger.info(
            "briefing_agent.eod_brief.done",
            sentiment=getattr(result, "sentiment", None),
        )
        return result
