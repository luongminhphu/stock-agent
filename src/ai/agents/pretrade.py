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
        user_id: str | None = None,
        trigger: str = "pretrade_check",
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
                            an investor profile block and memory context is injected.
                            Pass None to skip (existing behaviour — backward compat).
            user_id:        Optional user_id for memory logging.
            trigger:        Trigger label for episodic log.
        """
        investor_profile = await self._build_investor_profile(session, user_id)
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
        # PreTradeCheckOutput has many long-form fields; use COMPLEX_MAX_TOKENS
        # (8192) to avoid mid-JSON truncation on verbose tickers.
        result: PreTradeCheckOutput = await self._client.chat(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            response_schema=PreTradeCheckOutput,
            max_tokens=AIClient.COMPLEX_MAX_TOKENS,
        )
        logger.info(
            "pretrade_agent.check.done",
            ticker=ticker,
            decision=getattr(result, "decision", None),
            confidence=getattr(result, "confidence", None),
        )

        # --- Memory: log interaction (Layer 2) ---
        await self._log_interaction(
            session=session,
            user_id=user_id,
            ticker=ticker,
            result=result,
            trigger=trigger,
        )

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _build_investor_profile(self, session, user_id: str | None) -> str:
        """Build investor profile block via ContextBuilder (includes memory).

        Returns empty string when session is None or any error occurs so
        the pre-trade check is never blocked by profile unavailability.
        Owner of assembly logic: ai.ContextBuilder (not this method).
        """
        if session is None:
            return ""
        try:
            from src.ai.context_builder import ContextBuilder, render_for_agent

            ctx = await ContextBuilder(session).build(user_id=user_id)
            return render_for_agent(ctx)
        except Exception as exc:
            logger.warning("pretrade_agent.investor_profile_failed", error=str(exc))
            return ""

    async def _log_interaction(
        self,
        session,
        user_id: str | None,
        ticker: str,
        result: PreTradeCheckOutput,
        trigger: str,
    ) -> None:
        """Fire-and-forget memory log. Never raises."""
        if session is None or not user_id:
            return
        try:
            from src.ai.memory.memory_service import InteractionEntry, MemoryService

            risk_lines = []
            for rs in (getattr(result, "risk_signals", []) or [])[:5]:
                risk_lines.append(str(rs) if not hasattr(rs, "signal") else str(rs.signal))

            key_lines = []
            for nw in (getattr(result, "next_watch", []) or [])[:3]:
                key_lines.append(str(nw))

            entry = InteractionEntry(
                user_id=user_id,
                agent_type="pretrade",
                trigger=trigger,
                tickers=[ticker],
                ai_verdict=str(getattr(result, "decision", "") or ""),
                ai_confidence=getattr(result, "confidence", None),
                ai_key_points="\n".join(key_lines) if key_lines else None,
                ai_risk_signals="\n".join(risk_lines) if risk_lines else None,
            )
            await MemoryService.log_interaction(session, entry)
        except Exception as exc:
            logger.warning(
                "pretrade_agent.memory_log_failed",
                ticker=ticker,
                error=str(exc),
            )
