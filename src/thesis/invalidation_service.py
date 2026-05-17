"""Invalidation service — checks whether a thesis should be auto-invalidated.

Owner: thesis segment.
Rules live here. Bot/scheduler triggers a check; they don't own the rules.

Watchdog health scores (from WatchdogService) feed into check_with_health()
for richer decisions, but check() remains the stable original interface.

ThesisInvalidationDetector integration:
  When detector is injected, check_with_ai() runs the rule check first.
  If should_invalidate=True, the AI confirmation layer is invoked for:
    1. Verdict: CONFIRMED / SUSPECTED / CLEARED.
    2. Investor-facing narrative for bot alert.
    3. Recommended action: exit_signal / review / reduce / hold.
  Non-blocking: any AI failure returns (rule_result, None) — brief unaffected.
  check() and check_with_price() are unchanged — zero breaking change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.platform.logging import get_logger
from src.thesis.models import AssumptionStatus, Thesis

if TYPE_CHECKING:
    from src.ai.agents.invalidation_detector import ThesisInvalidationDetector
    from src.ai.schemas.invalidation import InvalidationSignal

logger = get_logger(__name__)

# Thresholds — adjust as product matures
_MAX_INVALID_ASSUMPTION_RATIO = 0.5  # >50% assumptions invalid → invalidate
_MIN_SCORE_THRESHOLD = 20.0          # score below 20 → warn
_STOP_LOSS_BREACH_BUFFER = 0.0       # current_price <= stop_loss → breach


@dataclass
class InvalidationCheckResult:
    should_invalidate: bool
    reason: str
    invalid_assumptions: list[str]
    score: float
    stop_loss_breached: bool = False


class InvalidationService:
    """Evaluate a thesis for auto-invalidation conditions.

    Does NOT write to the DB — returns a result for the caller
    (ThesisService or a scheduled job) to act on.

    Args:
        detector: optional ThesisInvalidationDetector — when provided,
                  check_with_ai() adds an AI confirmation layer on top of
                  the rule-based result. Pass None to skip AI layer (default).
    """

    def __init__(
        self,
        detector: ThesisInvalidationDetector | None = None,
    ) -> None:
        self._detector = detector

    def check(self, thesis: Thesis, current_score: float) -> InvalidationCheckResult:
        """Original interface — unchanged, backward compatible."""
        invalid_assumptions = [
            a.description for a in thesis.assumptions if a.status == AssumptionStatus.INVALID
        ]

        # Rule 1: too many invalid assumptions
        if thesis.assumptions:
            ratio = len(invalid_assumptions) / len(thesis.assumptions)
            if ratio > _MAX_INVALID_ASSUMPTION_RATIO:
                return InvalidationCheckResult(
                    should_invalidate=True,
                    reason=(
                        f"{len(invalid_assumptions)}/{len(thesis.assumptions)} "
                        f"assumptions invalid (>{_MAX_INVALID_ASSUMPTION_RATIO:.0%} threshold)"
                    ),
                    invalid_assumptions=invalid_assumptions,
                    score=current_score,
                )

        return InvalidationCheckResult(
            should_invalidate=False,
            reason="No invalidation conditions met",
            invalid_assumptions=invalid_assumptions,
            score=current_score,
        )

    def check_with_price(
        self,
        thesis: Thesis,
        current_score: float,
        current_price: float | None = None,
    ) -> InvalidationCheckResult:
        """Extended check with Rule 2: stop-loss breach detection.

        Supersedes the Wave 3 placeholder comment in the original check().
        Falls back to check() if current_price is None.
        """
        if current_price is None:
            return self.check(thesis, current_score)

        invalid_assumptions = [
            a.description for a in thesis.assumptions if a.status == AssumptionStatus.INVALID
        ]

        # Rule 2: stop-loss breached
        stop_loss_breached = bool(
            thesis.stop_loss
            and current_price <= thesis.stop_loss + _STOP_LOSS_BREACH_BUFFER
        )
        if stop_loss_breached:
            logger.info(
                "invalidation.stop_loss_breached",
                thesis_id=thesis.id,
                ticker=thesis.ticker,
                current_price=current_price,
                stop_loss=thesis.stop_loss,
            )
            return InvalidationCheckResult(
                should_invalidate=True,
                reason=(
                    f"Stop-loss breached: giá hiện tại {current_price:,.0f} ≤ "
                    f"stop-loss {thesis.stop_loss:,.0f}"
                ),
                invalid_assumptions=invalid_assumptions,
                score=current_score,
                stop_loss_breached=True,
            )

        # Rule 1: too many invalid assumptions
        if thesis.assumptions:
            ratio = len(invalid_assumptions) / len(thesis.assumptions)
            if ratio > _MAX_INVALID_ASSUMPTION_RATIO:
                return InvalidationCheckResult(
                    should_invalidate=True,
                    reason=(
                        f"{len(invalid_assumptions)}/{len(thesis.assumptions)} "
                        f"assumptions invalid (>{_MAX_INVALID_ASSUMPTION_RATIO:.0%} threshold)"
                    ),
                    invalid_assumptions=invalid_assumptions,
                    score=current_score,
                )

        return InvalidationCheckResult(
            should_invalidate=False,
            reason="No invalidation conditions met",
            invalid_assumptions=invalid_assumptions,
            score=current_score,
            stop_loss_breached=False,
        )

    async def check_with_ai(
        self,
        thesis: Thesis,
        current_score: float,
        current_price: float | None = None,
        watchdog_verdict: str | None = None,
        watchdog_urgency: str | None = None,
    ) -> tuple[InvalidationCheckResult, InvalidationSignal | None]:
        """Rule check + optional AI confirmation layer.

        Flow:
          1. Run check_with_price() — pure rule-based, always fast.
          2. If should_invalidate=False or detector=None → return (result, None).
          3. If should_invalidate=True and detector injected → await detector.detect().
          4. detector.detect() is non-blocking: AI errors return fallback signal
             with confidence=0.3 — never raises.

        Returns:
            (InvalidationCheckResult, InvalidationSignal | None)
            signal is None when rule did not trigger or detector was not injected.

        Args:
            thesis:           Thesis to evaluate.
            current_score:    Current thesis score (0-100).
            current_price:    Current market price (VND). None → skip price rules.
            watchdog_verdict: Optional WatchdogOutput verdict string for AI context.
            watchdog_urgency: Optional WatchdogOutput urgency string for AI context.
        """
        rule_result = self.check_with_price(thesis, current_score, current_price)

        if not rule_result.should_invalidate or self._detector is None:
            return rule_result, None

        logger.info(
            "invalidation.ai_confirmation_start",
            thesis_id=thesis.id,
            ticker=thesis.ticker,
            reason=rule_result.reason,
        )

        try:
            signal = await self._detector.detect(
                thesis_id=thesis.id,
                ticker=thesis.ticker,
                thesis_title=thesis.title,
                thesis_summary=thesis.summary or "",
                breach_reason=rule_result.reason,
                stop_loss_breached=rule_result.stop_loss_breached,
                current_price=current_price,
                stop_loss=thesis.stop_loss,
                invalid_assumptions=rule_result.invalid_assumptions,
                total_assumptions=len(thesis.assumptions),
                score=rule_result.score,
                watchdog_verdict=watchdog_verdict,
                watchdog_urgency=watchdog_urgency,
            )
            logger.info(
                "invalidation.ai_confirmation_done",
                thesis_id=thesis.id,
                ticker=thesis.ticker,
                verdict=signal.verdict,
                action=signal.action,
                confidence=signal.confidence,
            )
            return rule_result, signal

        except Exception as exc:
            logger.warning(
                "invalidation.ai_confirmation_failed thesis_id=%s ticker=%s: %s",
                thesis.id,
                thesis.ticker,
                exc,
            )
            return rule_result, None
