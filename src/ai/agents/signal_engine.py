"""SignalEngineAgent — cross-watchlist × thesis × portfolio orchestrator.

Owner: ai segment.
Responsibility:
  - Nhận output đã có từ WatchdogAgent + StressTestAgent (không re-run chúng).
  - Đọc portfolio context từ readmodel.PortfolioQueryService output.
  - Dùng AI để rank signals, identify thesis_review_triggers, viết portfolio_risk_note.
  - Output: SignalEngineOutput → inject vào BriefingAgent context (inline, không phải scheduler).

Boundary:
  - KHÔNG gọi market API trực tiếp.
  - KHÔNG chứa domain rule cứng về thesis hay portfolio.
  - KHÔNG schedule ThesisReview trực tiếp — chỉ trả thesis_review_triggers để caller quyết định.
  - bot và api KHÔNG gọi agent này trực tiếp — chỉ qua BriefingService.

Changelog:
  - Added feedback_summary param (optional, backward-compat, default="").
    Passed through to build_user_prompt → AI calibrates urgency/confidence
    against user's historical acted/ignored/disagreed patterns (rule 13).
  - Deepened thesis cross-check: agent now passes full thesis dicts to
    build_user_prompt. Callers should include assumptions/catalysts/
    invalidation_conditions in active_theses for deep cross-check.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.ai.client import AIClient
from src.ai.prompts.signal_engine import SPEC, build_user_prompt
from src.ai.schemas import (
    PortfolioRiskNote,
    RankedSignal,
    SignalEngineOutput,
    SignalUrgency,
    Verdict,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Urgency sort order — CRITICAL first
_URGENCY_ORDER = {
    SignalUrgency.CRITICAL: 0,
    SignalUrgency.HIGH: 1,
    SignalUrgency.MEDIUM: 2,
    SignalUrgency.LOW: 3,
}


def _build_portfolio_context(portfolio_data: dict[str, Any]) -> PortfolioRiskNote:
    """Extract structured risk context from PortfolioQueryService.get_portfolio() output.

    Rule-based — no LLM needed. Thresholds:
      - Concentration: weight_pct > 25%
      - Losing: pnl_pct < -5%
      - Misaligned: holding position but last_verdict is BEARISH
    """
    positions: list[dict] = portfolio_data.get("positions", [])

    top_concentration = [
        p["ticker"]
        for p in positions
        if (p.get("weight_pct") or 0) > 25
    ]
    losing_positions = [
        p["ticker"]
        for p in positions
        if (p.get("pnl_pct") or 0) < -5
    ]
    misaligned_positions = [
        p["ticker"]
        for p in positions
        if p.get("last_verdict") == "BEARISH"
        and p.get("quantity") is not None
    ]

    return PortfolioRiskNote(
        top_concentration=top_concentration,
        losing_positions=losing_positions,
        misaligned_positions=misaligned_positions,
        total_pnl_pct=portfolio_data.get("total_pnl_pct"),
        position_count=portfolio_data.get("position_count", 0),
    )


class SignalEngineAgent:
    """Orchestrates watchdog × stress_test × portfolio → ranked SignalEngineOutput.

    Intended call site: BriefingService.run(), inline before BriefingAgent LLM call.

    Example usage::

        engine = SignalEngineAgent(ai_client)
        signal_output = await engine.run(
            watchdog_outputs=watchdog_results,
            stress_outputs=stress_results,
            active_theses=thesis_summaries,  # include assumptions/catalysts for deep cross-check
            portfolio_data=await portfolio_query_service.get_portfolio(user_id, price_map),
            feedback_summary=await feedback_service.render_calibration_string(user_id),
        )
        # inject signal_output into BriefingAgent context
    """

    def __init__(self, ai_client: AIClient) -> None:
        self._client = ai_client

    async def run(
        self,
        *,
        watchdog_outputs: list[dict[str, Any]],
        stress_outputs: list[dict[str, Any]],
        active_theses: list[dict[str, Any]],
        portfolio_data: dict[str, Any] | None = None,
        feedback_summary: str = "",
    ) -> SignalEngineOutput:
        """Run signal engine. Returns SignalEngineOutput.

        Fallback: if AI call fails, returns best-effort output built from
        watchdog_outputs using rule-based logic only (no AI).

        Args:
            watchdog_outputs:  list of WatchdogOutput.model_dump() per ticker.
            stress_outputs:    list of StressTestOutput.model_dump() per ticker.
            active_theses:     list of thesis summary dicts. For deep thesis cross-check
                               (prompt rule 12), include: assumptions, catalysts,
                               invalidation_conditions alongside id/ticker/title/status/score.
                               Shallow dicts still work — cross-check degrades gracefully.
            portfolio_data:    PortfolioQueryService.get_portfolio() output. None = skip.
            feedback_summary:  Pre-rendered calibration string from FeedbackService.
                               Empty string = skip feedback calibration (rule 13).
                               Example: "acted_rate=0.3 | ignored_sectors=[banking] |
                                         regret_ignores=2 | total_events=8"
        """
        generated_at = datetime.now(UTC).isoformat()
        portfolio_context = (
            _build_portfolio_context(portfolio_data)
            if portfolio_data
            else PortfolioRiskNote()
        )

        user_prompt = build_user_prompt(
            watchdog_outputs=watchdog_outputs,
            stress_outputs=stress_outputs,
            active_theses=active_theses,
            portfolio_risk_context=portfolio_context.model_dump(),
            generated_at=generated_at,
            feedback_summary=feedback_summary,
        )

        try:
            raw: SignalEngineOutput = await self._client.structured_call(
                spec=SPEC,
                user_prompt=user_prompt,
            )
            # Enforce sort + cap regardless of what AI returned
            raw.ranked_signals.sort(
                key=lambda s: _URGENCY_ORDER.get(s.urgency, 9)
            )
            raw.ranked_signals = raw.ranked_signals[:10]
            # Always use rule-based portfolio context — never trust AI-rewritten version
            raw.portfolio_context = portfolio_context
            raw.generated_at = generated_at
            logger.info(
                "SignalEngine: %d signals, %d review triggers, feedback_calibrated=%s — %s",
                len(raw.ranked_signals),
                len(raw.thesis_review_triggers),
                bool(feedback_summary.strip()),
                raw.signal_summary,
            )
            return raw

        except Exception as exc:
            logger.warning(
                "SignalEngineAgent AI call failed, using rule-based fallback: %s", exc
            )
            return self._fallback(
                watchdog_outputs=watchdog_outputs,
                portfolio_context=portfolio_context,
                generated_at=generated_at,
            )

    def _fallback(
        self,
        *,
        watchdog_outputs: list[dict[str, Any]],
        portfolio_context: PortfolioRiskNote,
        generated_at: str,
    ) -> SignalEngineOutput:
        """Rule-based fallback when AI is unavailable.

        Converts watchdog verdicts directly to RankedSignals without AI synthesis.
        Confidence is set low (0.4) to signal degraded quality to downstream consumers.
        """
        signals: list[RankedSignal] = []

        for w in watchdog_outputs:
            ticker = w.get("ticker", "")
            if not ticker:
                continue

            verdict_str = w.get("verdict", "NEUTRAL")
            try:
                verdict = Verdict(verdict_str)
            except ValueError:
                verdict = Verdict.NEUTRAL

            # Map watchdog health score → urgency when available
            health_score = w.get("health_score") or 0
            if health_score < 40 or verdict == Verdict.BEARISH:
                urgency = SignalUrgency.HIGH
            elif health_score < 70:
                urgency = SignalUrgency.MEDIUM
            else:
                urgency = SignalUrgency.LOW

            signals.append(
                RankedSignal(
                    ticker=ticker,
                    urgency=urgency,
                    verdict=verdict,
                    thesis_aligned=False,  # can't determine without AI
                    trigger_reason=w.get("summary", "Watchdog alert — AI unavailable"),
                    risk_flags=w.get("risk_flags", []),
                    action="Review manually — signal engine AI unavailable",
                    causal_sources=[f"watchdog:{ticker}"],
                    confidence=w.get("confidence", 0.5),
                )
            )

        signals.sort(key=lambda s: _URGENCY_ORDER.get(s.urgency, 9))

        return SignalEngineOutput(
            generated_at=generated_at,
            ranked_signals=signals[:10],
            portfolio_context=portfolio_context,
            confidence=0.4,
        )
