"""Scoring service — computes a 0–100 score for a thesis.

Owner: thesis segment.
Scoring rules live here, not in the AI layer.
Wave 3: enrich with AI-assisted scoring signals.
"""
from __future__ import annotations

from src.thesis.models import AssumptionStatus, CatalystStatus, Thesis
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Weights must sum to 1.0
_WEIGHTS = {
    "assumption_health": 0.40,
    "catalyst_progress": 0.30,
    "risk_reward": 0.20,
    "review_confidence": 0.10,
}


class ScoringService:
    """Compute a composite thesis score (0–100).

    Higher score = thesis is healthier / more likely to play out.
    """

    def compute(self, thesis: Thesis) -> float:
        score = 0.0

        # 1. Assumption health (40%)
        if thesis.assumptions:
            valid = sum(1 for a in thesis.assumptions if a.status == AssumptionStatus.VALID)
            invalid = sum(1 for a in thesis.assumptions if a.status == AssumptionStatus.INVALID)
            total = len(thesis.assumptions)
            # Penalise hard for invalids
            assumption_score = max(0.0, (valid - invalid * 2) / total)
            score += assumption_score * _WEIGHTS["assumption_health"] * 100
        else:
            score += 50 * _WEIGHTS["assumption_health"]  # neutral when no assumptions

        # 2. Catalyst progress (30%)
        if thesis.catalysts:
            triggered = sum(
                1 for c in thesis.catalysts if c.status == CatalystStatus.TRIGGERED
            )
            catalyst_score = triggered / len(thesis.catalysts)
            score += catalyst_score * _WEIGHTS["catalyst_progress"] * 100
        else:
            score += 50 * _WEIGHTS["catalyst_progress"]

        # 3. Risk/reward (20%)
        rr = thesis.risk_reward
        if rr is not None:
            # 3:1 R/R = full marks; scale linearly, cap at 1.0
            rr_score = min(rr / 3.0, 1.0)
            score += rr_score * _WEIGHTS["risk_reward"] * 100
        else:
            score += 50 * _WEIGHTS["risk_reward"]

        # 4. Latest review confidence (10%)
        if thesis.reviews:
            latest = max(thesis.reviews, key=lambda r: r.reviewed_at)
            score += latest.confidence * _WEIGHTS["review_confidence"] * 100
        else:
            score += 50 * _WEIGHTS["review_confidence"]

        result = round(min(max(score, 0.0), 100.0), 2)
        logger.debug("thesis.score_computed", thesis_id=thesis.id, score=result)
        return result
