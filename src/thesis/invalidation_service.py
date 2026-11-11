"""Invalidation service — kiểm tra thesis có nên tự động vô hiệu hoá không.

Owner: thesis segment. Thuần luật, không gọi AI, không ghi DB — trả kết quả để
caller (ThesisComponentService) quyết định.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.thesis.models import AssumptionStatus, Thesis

# >50% giả định đã sai → vô hiệu hoá
_MAX_INVALID_ASSUMPTION_RATIO = 0.5


@dataclass
class InvalidationCheckResult:
    should_invalidate: bool
    reason: str
    invalid_assumptions: list[str]
    score: float


class InvalidationService:
    """Đánh giá thesis theo luật vô hiệu hoá."""

    def check(self, thesis: Thesis, current_score: float) -> InvalidationCheckResult:
        invalid_assumptions = [
            a.description for a in thesis.assumptions if a.status == AssumptionStatus.INVALID
        ]
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
