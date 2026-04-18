"""Invalidation service — checks whether a thesis should be auto-invalidated.

Owner: thesis segment.
Rules live here. Bot/scheduler triggers a check; they don't own the rules.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.thesis.models import AssumptionStatus, Thesis
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Thresholds — adjust as product matures
_MAX_INVALID_ASSUMPTION_RATIO = 0.5   # >50% assumptions invalid → invalidate
_MIN_SCORE_THRESHOLD = 20.0           # score below 20 → warn


@dataclass
class InvalidationCheckResult:
    should_invalidate: bool
    reason: str
    invalid_assumptions: list[str]
    score: float


class InvalidationService:
    """Evaluate a thesis for auto-invalidation conditions.

    Does NOT write to the DB — returns a result for the caller
    (ThesisService or a scheduled job) to act on.
    """

    def check(self, thesis: Thesis, current_score: float) -> InvalidationCheckResult:
        invalid_assumptions = [
            a.description
            for a in thesis.assumptions
            if a.status == AssumptionStatus.INVALID
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

        # Rule 2: stop-loss breached (price check done by caller, passed as score signal)
        # Placeholder — Wave 3 will inject current_price and compare vs stop_loss

        return InvalidationCheckResult(
            should_invalidate=False,
            reason="No invalidation conditions met",
            invalid_assumptions=invalid_assumptions,
            score=current_score,
        )
