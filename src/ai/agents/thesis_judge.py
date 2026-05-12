"""ThesisJudgeAgent — automated thesis × signal cross-check.

Owner: ai segment.
Trigger: SignalEngineOutput.thesis_review_triggers (emitted by SignalEngineAgent).
Caller: BriefingService — runs batch after SignalEngine, before BriefingAgent LLM call.

Distinct from ThesisReviewAgent:
  - ThesisReviewAgent: user-initiated, full context, deep analysis, writes to UI.
  - ThesisJudgeAgent:  auto-triggered, fast cross-check, feeds briefing + readmodel.

Responsibility:
  - Receives signal_context (watchdog/stress verdict) + thesis metadata.
  - Produces ThesisJudgeOutput per thesis: verdict, conviction_delta,
    challenged_assumptions, new_risks, action, reasoning.
  - run_batch(): processes multiple triggered theses concurrently.
  - Does NOT write to DB — caller (BriefingService) owns persistence decision.
  - Does NOT trigger ThesisReviewAgent — only signals action="review" for caller.

Boundary:
  - ONLY reads thesis metadata passed in — no DB calls.
  - ONLY uses signal_context passed in — no market API calls.
  - bot and api NEVER call this directly — only through BriefingService.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from src.ai.client import AIClient
from src.ai.prompts.thesis_judge import SPEC, build_user_prompt
from src.ai.schemas import (
    ChallengedAssumption,
    ThesisJudgeOutput,
    ThesisJudgeVerdict,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Fallback helpers
# ---------------------------------------------------------------------------

_BEARISH_VERDICTS = {"BEARISH", "bearish"}
_CRITICAL_URGENCIES = {"CRITICAL", "critical"}
_HIGH_URGENCIES = {"HIGH", "high"}


def _derive_fallback_verdict(
    watchdog_verdict: str | None,
    signal_urgency: str | None,
) -> tuple[ThesisJudgeVerdict, float, str]:
    """Rule-based verdict when AI is unavailable.

    Returns: (verdict, conviction_delta, action)
    """
    is_bearish = watchdog_verdict in _BEARISH_VERDICTS
    is_critical = signal_urgency in _CRITICAL_URGENCIES
    is_high = signal_urgency in _HIGH_URGENCIES

    if is_bearish and is_critical:
        return ThesisJudgeVerdict.INVALIDATED, -0.6, "exit_signal"
    if is_bearish or is_critical:
        return ThesisJudgeVerdict.WEAKENING, -0.35, "review"
    if is_high:
        return ThesisJudgeVerdict.WEAKENING, -0.2, "reduce"
    return ThesisJudgeVerdict.ON_TRACK, 0.0, "hold"


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class ThesisJudgeAgent:
    """Runs fast thesis × signal cross-check for auto-triggered review.

    Intended call site: BriefingService, after SignalEngineAgent.run(),
    before BriefingAgent LLM call.

    Example usage::

        judge = ThesisJudgeAgent(ai_client)

        # Build trigger inputs from signal_output + thesis repo
        trigger_inputs = [
            ThesisJudgeTrigger(
                thesis_id="42",
                ticker="VHM",
                thesis_title="VHM phục hồi sau chu kỳ margin call",
                thesis_summary="...",
                assumptions=[{"id": 1, "description": "Lãi suất giảm Q3"}],
                catalysts=[{"id": 3, "description": "KQKD Q2 > kỳ vọng"}],
                invalidation_conditions=["Margin call lần 2", "P/B vượt 2.0x"],
                signal_context={
                    "watchdog_verdict": "BEARISH",
                    "urgency": "HIGH",
                    "trigger_reason": "Dòng tiền khối ngoại bán ròng 3 phiẫn",
                    "risk_flags": ["volume_spike", "foreign_sell"],
                },
            )
        ]

        results = await judge.run_batch(trigger_inputs)
        # results: list[ThesisJudgeOutput] — inject into BriefingAgent context
    """

    def __init__(self, ai_client: AIClient) -> None:
        self._client = ai_client

    async def run(
        self,
        *,
        thesis_id: str | int,
        ticker: str,
        thesis_title: str,
        thesis_summary: str,
        assumptions: list[dict[str, Any]],
        catalysts: list[dict[str, Any]],
        invalidation_conditions: list[str],
        signal_context: dict[str, Any],
        conviction_history: list[dict[str, Any]] | None = None,
        days_since_written: int | None = None,
    ) -> ThesisJudgeOutput:
        """Run a single thesis judge check. Returns ThesisJudgeOutput.

        Fallback: if AI call fails, returns rule-based output derived from
        watchdog_verdict + signal_urgency. Confidence=0.3 signals degraded quality.

        Args:
            thesis_id:               Thesis ID for traceability.
            ticker:                  Mã cổ phiếu.
            thesis_title:            Tiêu đề thesis.
            thesis_summary:          Tóm tắt luận điểm.
            assumptions:             Active assumptions [{"id", "description", "status"}].
            catalysts:               Pending catalysts [{"id", "description", "status"}].
            invalidation_conditions: Explicit conditions that would kill the thesis.
            signal_context:          Signal data from SignalEngine / Watchdog output.
                                     Expected keys: watchdog_verdict, urgency,
                                     trigger_reason, risk_flags, health_score,
                                     stress_verdict, signal_summary.
            conviction_history:      Last N judge verdicts for trend context.
            days_since_written:      Days since thesis was created.
        """
        user_prompt = build_user_prompt(
            thesis_id=thesis_id,
            ticker=ticker,
            thesis_title=thesis_title,
            thesis_summary=thesis_summary,
            assumptions=assumptions,
            catalysts=catalysts,
            invalidation_conditions=invalidation_conditions,
            signal_context=signal_context,
            conviction_history=conviction_history,
            days_since_written=days_since_written,
        )

        try:
            result: ThesisJudgeOutput = await self._client.structured_call(
                spec=SPEC,
                user_prompt=user_prompt,
            )
            # Stamp thesis_id on result for downstream consumers
            result.thesis_id = str(thesis_id)
            result.ticker = ticker
            result.judged_at = datetime.now(UTC).isoformat()

            logger.info(
                "ThesisJudge: thesis=%s ticker=%s verdict=%s delta=%+.2f action=%s",
                thesis_id,
                ticker,
                result.verdict,
                result.conviction_delta,
                result.action,
            )
            return result

        except Exception as exc:
            logger.warning(
                "ThesisJudgeAgent AI call failed for thesis=%s ticker=%s, "
                "using rule-based fallback: %s",
                thesis_id,
                ticker,
                exc,
            )
            return self._fallback(
                thesis_id=thesis_id,
                ticker=ticker,
                signal_context=signal_context,
            )

    async def run_batch(
        self,
        triggers: list[dict[str, Any]],
    ) -> list[ThesisJudgeOutput]:
        """Run thesis judge for multiple triggers concurrently.

        Each trigger is a dict with keys matching run() kwargs.
        Per-thesis errors are caught and replaced with fallback output —
        one failure never blocks the rest of the batch.

        Args:
            triggers: list of dicts, each with keys:
                thesis_id, ticker, thesis_title, thesis_summary,
                assumptions, catalysts, invalidation_conditions,
                signal_context, conviction_history (optional),
                days_since_written (optional).

        Returns:
            list[ThesisJudgeOutput] in same order as triggers.
        """
        if not triggers:
            return []

        async def _run_one(t: dict[str, Any]) -> ThesisJudgeOutput:
            try:
                return await self.run(
                    thesis_id=t["thesis_id"],
                    ticker=t["ticker"],
                    thesis_title=t.get("thesis_title", ""),
                    thesis_summary=t.get("thesis_summary", ""),
                    assumptions=t.get("assumptions", []),
                    catalysts=t.get("catalysts", []),
                    invalidation_conditions=t.get("invalidation_conditions", []),
                    signal_context=t.get("signal_context", {}),
                    conviction_history=t.get("conviction_history"),
                    days_since_written=t.get("days_since_written"),
                )
            except Exception as exc:
                # Should not reach here (run() has its own try/except),
                # but guard at batch level for absolute safety.
                logger.error(
                    "ThesisJudge batch: unexpected error for thesis=%s ticker=%s: %s",
                    t.get("thesis_id", "?"),
                    t.get("ticker", "?"),
                    exc,
                )
                return self._fallback(
                    thesis_id=t.get("thesis_id", "unknown"),
                    ticker=t.get("ticker", ""),
                    signal_context=t.get("signal_context", {}),
                )

        results = await asyncio.gather(*[_run_one(t) for t in triggers])
        logger.info(
            "ThesisJudge batch complete: %d theses processed",
            len(results),
        )
        return list(results)

    def _fallback(
        self,
        *,
        thesis_id: str | int,
        ticker: str,
        signal_context: dict[str, Any],
    ) -> ThesisJudgeOutput:
        """Rule-based fallback when AI is unavailable.

        Derives verdict from watchdog_verdict + signal_urgency.
        Confidence=0.3 signals degraded quality to downstream consumers.
        challenged_assumptions is empty — cannot determine without AI.
        """
        watchdog_verdict = signal_context.get("watchdog_verdict")
        signal_urgency = signal_context.get("urgency")
        trigger_reason = signal_context.get("trigger_reason", "AI unavailable — rule-based fallback")

        verdict, conviction_delta, action = _derive_fallback_verdict(
            watchdog_verdict=watchdog_verdict,
            signal_urgency=signal_urgency,
        )

        return ThesisJudgeOutput(
            thesis_id=str(thesis_id),
            ticker=ticker,
            verdict=verdict,
            conviction_delta=conviction_delta,
            challenged_assumptions=[],  # cannot determine without AI
            new_risks=(
                [trigger_reason]
                if verdict in (ThesisJudgeVerdict.WEAKENING, ThesisJudgeVerdict.INVALIDATED)
                else []
            ),
            action=action,
            reasoning=f"Rule-based fallback — AI unavailable. Derived from: "
                       f"watchdog={watchdog_verdict}, urgency={signal_urgency}.",
            confidence=0.3,
            judged_at=datetime.now(UTC).isoformat(),
        )
