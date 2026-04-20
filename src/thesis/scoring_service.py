"""Scoring service — computes a 0–100 health score for a thesis.

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

# ---------------------------------------------------------------------------
# Score tier — contextual label for a 0-100 health score
# ---------------------------------------------------------------------------

# Each entry: (lo_inclusive, hi_inclusive, label, icon)
SCORE_TIERS: list[tuple[int, int, str, str]] = [
    (0,  30,  "Critical",  "🔴"),
    (31, 50,  "Weak",      "🟠"),
    (51, 70,  "Moderate",  "🟡"),
    (71, 85,  "Healthy",   "🟢"),
    (86, 100, "Strong",    "💎"),
]

# Max possible contribution per dimension (for display purposes)
SCORE_MAX: dict[str, float] = {
    "assumption_health": _WEIGHTS["assumption_health"] * 100,   # 40
    "catalyst_progress": _WEIGHTS["catalyst_progress"] * 100,   # 30
    "risk_reward":       _WEIGHTS["risk_reward"] * 100,         # 20
    "review_confidence": _WEIGHTS["review_confidence"] * 100,   # 10
}


def score_tier(score: float) -> tuple[str, str]:
    """Return (label, icon) for a given 0-100 score.

    Examples:
        score_tier(20.1) → ("Critical", "🔴")
        score_tier(75.0) → ("Healthy",  "🟢")
    """
    s = int(score)
    for lo, hi, label, icon in SCORE_TIERS:
        if lo <= s <= hi:
            return label, icon
    return "Unknown", "⚪"


class ScoringService:
    """Compute a composite thesis health score (0–100).

    Higher score = thesis is healthier / more likely to play out.
    """

    def compute(self, thesis: Thesis) -> float:
        """Return the total composite score (backward-compatible)."""
        total, _ = self.compute_with_breakdown(thesis)
        return total

    def compute_with_breakdown(
        self, thesis: Thesis
    ) -> tuple[float, dict[str, float]]:
        """Return (total_score, breakdown_dict) where breakdown shows
        the weighted contribution of each dimension (sums to total).

        Breakdown keys: assumption_health, catalyst_progress,
                        risk_reward, review_confidence.
        Each value is the score contribution (0 to weight*100).
        """
        breakdown: dict[str, float] = {}

        # 1. Assumption health (40%)
        if thesis.assumptions:
            valid = sum(1 for a in thesis.assumptions if a.status == AssumptionStatus.VALID)
            invalid = sum(1 for a in thesis.assumptions if a.status == AssumptionStatus.INVALID)
            total_a = len(thesis.assumptions)
            raw = max(0.0, (valid - invalid * 2) / total_a)
            breakdown["assumption_health"] = round(raw * _WEIGHTS["assumption_health"] * 100, 2)
        else:
            breakdown["assumption_health"] = round(50 * _WEIGHTS["assumption_health"], 2)

        # 2. Catalyst progress (30%)
        if thesis.catalysts:
            triggered = sum(1 for c in thesis.catalysts if c.status == CatalystStatus.TRIGGERED)
            raw = triggered / len(thesis.catalysts)
            breakdown["catalyst_progress"] = round(raw * _WEIGHTS["catalyst_progress"] * 100, 2)
        else:
            breakdown["catalyst_progress"] = round(50 * _WEIGHTS["catalyst_progress"], 2)

        # 3. Risk/reward (20%)
        rr = thesis.risk_reward
        if rr is not None:
            raw = min(rr / 3.0, 1.0)
            breakdown["risk_reward"] = round(raw * _WEIGHTS["risk_reward"] * 100, 2)
        else:
            breakdown["risk_reward"] = round(50 * _WEIGHTS["risk_reward"], 2)

        # 4. Latest review confidence (10%)
        if thesis.reviews:
            latest = max(thesis.reviews, key=lambda r: (r.reviewed_at, r.id))
            breakdown["review_confidence"] = round(
                latest.confidence * _WEIGHTS["review_confidence"] * 100, 2
            )
        else:
            breakdown["review_confidence"] = round(50 * _WEIGHTS["review_confidence"], 2)

        total = round(
            min(max(sum(breakdown.values()), 0.0), 100.0), 2
        )
        logger.debug(
            "thesis.health_score_computed",
            thesis_id=thesis.id,
            total=total,
            breakdown=breakdown,
        )
        return total, breakdown
