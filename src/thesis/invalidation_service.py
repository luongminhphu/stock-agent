"""Invalidation service — checks whether a thesis should be auto-invalidated.

Owner: thesis segment.
Rules live here. Bot/scheduler triggers a check; they don't own the rules.

Watchdog health scores (from WatchdogService) feed into check_with_health()
for richer decisions, but check() remains the stable original interface.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.platform.logging import get_logger
from src.thesis.models import AssumptionStatus, Thesis

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
    """

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
