"""ReplayAgent — analyze past investor decisions and generate learning feedback.

Owner: ai segment. Một agent duy nhất cho 2 entry point (hợp nhất từ
``replay_agent.py`` — Wave F1):

  analyze(ctx)          ← thesis.decision_service (replay theo horizon)
                           → DecisionReplayResult (không ghi DB ngoài memory log)
  run_for_trade(...)    ← portfolio.trade_usecase sau SELL (fire-and-forget)
                           → ReplayOutcomeRecord + LessonService.persist_replay()

Loop position:
  decision/trade → ReplayAgent → lesson/pattern → MemoryContext → brief/pretrade

Boundary:
- Accepts ReplayContext (hoặc dict snapshot của trade — không import ORM portfolio).
- AI call duy nhất qua REPLAY_SPEC (src/ai/prompts/replay.py).

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

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.ai.client import AIClient
from src.ai.prompts.replay import SPEC as REPLAY_SPEC
from src.ai.prompts.replay import ReplayContext, build_user_prompt
from src.ai.schemas.replay import ReplayOutcomeRecord, ReplayOutput
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
        self.pattern_detected = output.pattern_tag.value if output.pattern_tag else None
        self.suggested_adjustment: str | None = None  # not in ReplayOutput schema
        self.confidence: float = output.confidence  # already float 0–1
        self.summary = output.summary
        self.thesis_accuracy_note = output.thesis_accuracy_note
        self.exit_reason_assessment = output.exit_reason_assessment


class ReplayAgent:
    def __init__(self, ai_client: AIClient) -> None:
        self._client = ai_client

    async def _call(self, ctx: ReplayContext) -> ReplayOutput:
        """AI call duy nhất — mọi entry point đi qua đây."""
        output: ReplayOutput = await self._client.chat(
            system_prompt=REPLAY_SPEC.system_prompt,
            user_prompt=build_user_prompt(ctx),
            response_schema=ReplayOutput,
            temperature=REPLAY_SPEC.temperature,
            max_tokens=REPLAY_SPEC.max_tokens,
        )
        return output

    # ------------------------------------------------------------------
    # Entry point 1: decision replay theo horizon (thesis.decision_service)
    # ------------------------------------------------------------------

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
            output = await self._call(ctx)
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

    # ------------------------------------------------------------------
    # Entry point 2: post-trade replay (portfolio SELL → lesson ledger)
    # ------------------------------------------------------------------

    async def run(
        self,
        session: AsyncSession,
        ctx: ReplayContext,
        user_id: str,
        trade_id: int,
    ) -> ReplayOutcomeRecord | None:
        """AI call → memory log → ReplayOutcomeRecord → LessonService.persist_replay().

        Returns None on any failure — fire-and-forget callers can ignore.
        """
        try:
            output = await self._call(ctx)
        except Exception as exc:
            logger.warning(
                "replay_agent.ai_call_failed",
                ticker=ctx.ticker,
                user_id=user_id,
                error=str(exc),
            )
            return None

        from src.ai.memory.lesson_service import LessonService
        from src.ai.memory.memory_service import InteractionEntry, MemoryService

        await MemoryService.log_interaction(
            session=session,
            entry=InteractionEntry(
                user_id=user_id,
                agent_type="replay",
                trigger="post_trade",
                tickers=[ctx.ticker],
                ai_verdict=output.outcome_verdict.value,
                ai_confidence=output.confidence,
                ai_key_points=(" | ".join(output.lessons[:3]) if output.lessons else None),
                ai_risk_signals=(output.pattern_tag.value if output.pattern_tag else None),
                thesis_id=ctx.thesis_id,
            ),
        )

        record = ReplayOutcomeRecord.from_replay_output(
            output=output,
            user_id=user_id,
            trade_id=trade_id,
        )
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

    async def run_for_trade(
        self,
        session: AsyncSession,
        user_id: str,
        trade_snapshot: dict[str, Any],
        thesis_snapshot: dict[str, Any] | None = None,
        brief_summary: str | None = None,
        outcome_horizon_days: int = 30,
    ) -> ReplayOutcomeRecord | None:
        """Build ReplayContext từ dict snapshot (không import ORM) rồi gọi run().

        trade_snapshot keys: id, ticker, traded_at, realized_pnl, price, exit_reason,
        entry_signal_ref. thesis_snapshot keys: thesis_id, score, health_score,
        rationale, active_signal. Thiếu key → None.
        """
        thesis = thesis_snapshot or {}
        traded_at = trade_snapshot.get("traded_at")
        if isinstance(traded_at, datetime):
            decision_at_str = traded_at.strftime("%Y-%m-%d %H:%M")
        else:
            decision_at_str = (
                str(traded_at) if traded_at else datetime.now(UTC).strftime("%Y-%m-%d")
            )

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
            outcome_price=None,
            outcome_pnl_pct=pnl_pct,
            outcome_horizon_days=outcome_horizon_days,
            outcome_verdict_hint=verdict_hint,
            exit_reason=trade_snapshot.get("exit_reason"),
            entry_signal_ref=trade_snapshot.get("entry_signal_ref"),
        )
        return await self.run(
            session=session, ctx=ctx, user_id=user_id, trade_id=trade_snapshot.get("id", 0)
        )


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


async def _log_replay_interaction(
    session: Any,
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
            ai_confidence=result.confidence,  # already float
            ai_key_points="\n".join(key_lines) if key_lines else None,
            ai_risk_signals="\n".join(risk_lines) if risk_lines else None,
            decision_id=result.decision_id,
        )
        await MemoryService.log_interaction(session, entry)
    except Exception as exc:
        logger.warning("replay_agent.memory_log_failed", error=str(exc))
