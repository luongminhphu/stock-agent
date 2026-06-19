"""ReplayAgent \u2014 post-mortem AI agent for closed trade decisions.

Owner: ai segment.
Callers:
  - bot/commands/replay_command.py (future) \u2014 /replay <ticker> command
  - portfolio usecase (SELL path) \u2014 fire-and-forget after trade closed

Loop position:
  Trade.SELL (portfolio)
  \u2192 ReplayAgent.run_for_trade()   \u2190 entry point from portfolio SELL usecase
  \u2192 ReplayAgent.run()             \u2190 core AI call
  \u2192 ReplayOutput (ai/schemas)     \u2190 structured AI output
  \u2192 ReplayOutcomeRecord built     \u2190 typed record
  \u2192 LessonService.persist_replay  \u2190 writes AIInteractionLog + UserBehaviorLog
  \u2192 MemoryContext.render()        \u2192 lessons surface in next brief prompt
  \u2192 briefing AI sees pattern      \u2192 prioritized_action enriched

Boundary rules:
  - MAY receive closed Trade data as a plain dict (not ORM object).
  - MUST NOT import from src.portfolio.models directly \u2014 use dict snapshot.
  - Calls LessonService.persist_replay() fire-and-forget (errors swallowed).
  - Calls MemoryService.log_interaction() for episode memory (existing pattern).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.memory.lesson_service import LessonService
from src.ai.memory.memory_service import InteractionEntry, MemoryService
from src.ai.prompts.replay import ReplayContext, build_user_prompt
from src.ai.prompts.replay import SPEC as REPLAY_SPEC
from src.ai.schemas.replay import ReplayOutcomeRecord, ReplayOutput
from src.platform.logging import get_logger

logger = get_logger(__name__)


class ReplayAgent:
    """Stateless agent \u2014 session passed per call.

    Usage (from portfolio SELL usecase)::

        agent = ReplayAgent(ai_client)
        asyncio.create_task(
            agent.run_for_trade(
                session=session,
                user_id=user_id,
                trade_snapshot={
                    'id': trade.id,
                    'ticker': trade.ticker,
                    'traded_at': trade.traded_at,
                    'realized_pnl': trade.realized_pnl,
                    'price': trade.price,
                    'exit_reason': trade.exit_reason.value if trade.exit_reason else None,
                    'entry_signal_ref': trade.entry_signal_ref,
                },
                thesis_snapshot={
                    'thesis_id': thesis.id,
                    'score': thesis.score,
                    'health_score': thesis.health_score,
                    'rationale': thesis.rationale,
                    'active_signal': thesis.active_signal,
                },
            )
        )
    """

    def __init__(self, client: Any) -> None:  # client: AIClient
        self._client = client

    # ------------------------------------------------------------------
    # Core run
    # ------------------------------------------------------------------

    async def run(
        self,
        session: AsyncSession,
        ctx: ReplayContext,
        user_id: str,
        trade_id: int,
    ) -> ReplayOutcomeRecord | None:
        """Run ReplayAgent for one ReplayContext.

        Steps:
          1. Call AI client with REPLAY_SPEC system prompt + user prompt.
          2. Parse ReplayOutput (Pydantic validation).
          3. Log interaction (MemoryService \u2014 existing pattern).
          4. Build ReplayOutcomeRecord.
          5. Fire-and-forget LessonService.persist_replay().
          6. Return ReplayOutcomeRecord (caller may use for display).

        Returns None on any failure \u2014 fire-and-forget callers can ignore.
        """
        try:
            output: ReplayOutput = await self._client.chat(
                system_prompt=REPLAY_SPEC.system_prompt,
                user_prompt=build_user_prompt(ctx),
                response_schema=REPLAY_SPEC.output_schema,
                max_tokens=REPLAY_SPEC.max_tokens,
            )
        except Exception as exc:
            logger.warning(
                "replay_agent.ai_call_failed",
                ticker=ctx.ticker,
                user_id=user_id,
                error=str(exc),
            )
            return None

        # Log interaction (episode memory)
        await MemoryService.log_interaction(
            session=session,
            entry=InteractionEntry(
                user_id=user_id,
                agent_type="replay",
                trigger="post_trade",
                tickers=[ctx.ticker],
                ai_verdict=output.outcome_verdict.value,
                ai_confidence=output.confidence,
                ai_key_points=(
                    " | ".join(output.lessons[:3]) if output.lessons else None
                ),
                ai_risk_signals=(
                    output.pattern_tag.value if output.pattern_tag else None
                ),
                thesis_id=ctx.thesis_id,
            ),
        )

        # Build typed record
        record = ReplayOutcomeRecord.from_replay_output(
            output=output,
            user_id=user_id,
            trade_id=trade_id,
        )

        # Persist lesson fire-and-forget
        asyncio.create_task(
            LessonService.persist_replay(record),
            name=f"lesson-{user_id}-{ctx.ticker}-{trade_id}",
        )

        logger.info(
            "replay_agent.run.ok",
            user_id=user_id,
            ticker=ctx.ticker,
            verdict=output.outcome_verdict,
            pattern_tag=output.pattern_tag,
            confidence=output.confidence,
            trade_id=trade_id,
        )
        return record

    # ------------------------------------------------------------------
    # Convenience entry point from SELL usecase
    # ------------------------------------------------------------------

    async def run_for_trade(
        self,
        session: AsyncSession,
        user_id: str,
        trade_snapshot: dict[str, Any],
        thesis_snapshot: dict[str, Any] | None = None,
        brief_summary: str | None = None,
        outcome_horizon_days: int = 30,
    ) -> ReplayOutcomeRecord | None:
        """Convenience method \u2014 builds ReplayContext from raw dicts.

        trade_snapshot required keys:
          id, ticker, traded_at (datetime | str), realized_pnl (float | None),
          price (float | None), exit_reason (str | None), entry_signal_ref (str | None)

        thesis_snapshot optional keys:
          thesis_id (int), score (float), health_score (int),
          rationale (str), active_signal (str)

        All missing keys degrade gracefully to None.
        """
        thesis = thesis_snapshot or {}
        traded_at = trade_snapshot.get("traded_at")
        if isinstance(traded_at, datetime):
            decision_at_str = traded_at.strftime("%Y-%m-%d %H:%M")
        else:
            decision_at_str = str(traded_at) if traded_at else datetime.now(UTC).strftime("%Y-%m-%d")

        realized_pnl = trade_snapshot.get("realized_pnl")
        entry_price = trade_snapshot.get("price")
        pnl_pct: float | None = None
        if realized_pnl is not None and entry_price and entry_price > 0:
            pnl_pct = (realized_pnl / entry_price) * 100

        verdict_hint: str | None = None
        if pnl_pct is not None:
            verdict_hint = "WIN" if pnl_pct > 0 else ("LOSS" if pnl_pct < 0 else "BREAK_EVEN")

        ctx = ReplayContext(
            decision_id=trade_snapshot.get("id", 0),
            thesis_id=thesis.get("thesis_id", 0),
            ticker=trade_snapshot["ticker"],
            decision_type="SELL",
            decision_at=decision_at_str,
            rationale=thesis.get("rationale") or "",
            price_at_decision=entry_price,
            thesis_score_at_decision=thesis.get("score"),
            thesis_health_score_at_decision=thesis.get("health_score"),
            active_signal=thesis.get("active_signal"),
            brief_summary=brief_summary,
            outcome_price=None,  # not available at close time
            outcome_pnl_pct=pnl_pct,
            outcome_horizon_days=outcome_horizon_days,
            outcome_verdict_hint=verdict_hint,
            exit_reason=trade_snapshot.get("exit_reason"),
            entry_signal_ref=trade_snapshot.get("entry_signal_ref"),
        )

        return await self.run(
            session=session,
            ctx=ctx,
            user_id=user_id,
            trade_id=trade_snapshot.get("id", 0),
        )
