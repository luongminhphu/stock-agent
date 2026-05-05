"""PreTradeAgent — AI agent for pre-trade cross-check.

Owner: ai segment.
Callers: thesis.PreTradeService (or equivalent command handler).

This agent cross-checks thesis, watchlist signal, today's brief, and
(optionally) investor profile + decision history to produce a structured
PreTradeCheckOutput before the investor places an order.

No business logic, no DB access, no Discord formatting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.ai.client import AIClient
from src.ai.prompts.pretrade import SYSTEM_PROMPT, build_pretrade_prompt
from src.ai.schemas import PreTradeCheckOutput
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class PreTradeAgent:
    """Performs AI-powered pre-trade cross-check.

    Args:
        client: Injected AIClient with retry/circuit-breaker.
    """

    def __init__(self, client: AIClient) -> None:
        self._client = client

    async def check(
        self,
        ticker: str,
        price: float,
        change_pct: float,
        thesis_context: str,
        signal_context: str,
        brief_context: str,
        past_lessons: str = "",
        session: AsyncSession | None = None,
    ) -> PreTradeCheckOutput:
        """Run pre-trade check for a single ticker.

        Args:
            ticker:         Ticker symbol (uppercase, e.g. 'VCB').
            price:          Current market price.
            change_pct:     % change from previous session.
            thesis_context: Active thesis summary for this ticker from ThesisService.
            signal_context: Watchlist scan signal context from WatchlistService.
            brief_context:  Today's brief mention for this ticker from BriefingService.
            past_lessons:   Optional formatted decision history from LessonService.
            session:        Optional AsyncSession. When provided, ContextBuilder builds
                            an investor profile block that is injected into the prompt
                            for personalised risk checks. Pass None to skip (existing
                            behaviour — backward compat preserved).
        """
        investor_profile = await self._build_investor_profile(session)
        prompt = build_pretrade_prompt(
            ticker=ticker,
            price=price,
            change_pct=change_pct,
            thesis_context=thesis_context,
            signal_context=signal_context,
            brief_context=brief_context,
            past_lessons=past_lessons,
            investor_profile=investor_profile,
        )
        logger.debug(
            "pretrade_agent.check.calling_ai",
            ticker=ticker,
            price=price,
            has_thesis=bool(thesis_context),
            has_signal=bool(signal_context),
            has_brief=bool(brief_context),
            has_lessons=bool(past_lessons),
            has_investor_profile=bool(investor_profile),
        )
        result: PreTradeCheckOutput = await self._client.chat(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            response_schema=PreTradeCheckOutput,
        )
        logger.info(
            "pretrade_agent.check.done",
            ticker=ticker,
            decision=getattr(result, "decision", None),
            confidence=getattr(result, "confidence", None),
        )
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _build_investor_profile(self, session: AsyncSession | None) -> str:
        """Build investor profile block via ContextBuilder.

        Returns empty string when session is None or any error occurs so
        the pre-trade check is never blocked by profile unavailability.
        Owner of assembly logic: ai.ContextBuilder (not this method).
        """
        if session is None:
            return ""
        try:
            from src.ai.context_builder import ContextBuilder, render_for_agent

            ctx = await ContextBuilder(session).build()
            return render_for_agent(ctx)
        except Exception as exc:
            logger.warning("pretrade_agent.investor_profile_failed", error=str(exc))
            return ""
