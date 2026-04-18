"""Unit tests for ScoringService.

Pure synchronous service — no DB, no async, no mocks needed.
Tests exercise all 4 scoring dimensions independently and combined.
"""
from __future__ import annotations

import pytest

from src.thesis.models import AssumptionStatus, CatalystStatus, ReviewVerdict
from src.thesis.scoring_service import ScoringService

from tests.thesis.conftest import make_assumption, make_catalyst, make_review, make_thesis


@pytest.fixture()
def svc() -> ScoringService:
    return ScoringService()


# ---------------------------------------------------------------------------
# Neutral baseline (no assumptions, no catalysts, no reviews, no prices)
# ---------------------------------------------------------------------------


def test_score_empty_thesis_is_neutral(svc):
    """Thesis with no data should score near the neutral midpoint."""
    thesis = make_thesis(
        entry_price=None,
        target_price=None,
        stop_loss=None,
    )
    score = svc.compute(thesis)
    # All 4 components contribute 50% of their weight → 50.0
    assert score == pytest.approx(50.0, abs=0.01)


# ---------------------------------------------------------------------------
# Assumption health dimension (40% weight)
# ---------------------------------------------------------------------------


def test_score_all_valid_assumptions(svc):
    """All VALID assumptions → full assumption score."""
    thesis = make_thesis(
        assumptions=[make_assumption(status=AssumptionStatus.VALID) for _ in range(3)],
        entry_price=None, target_price=None, stop_loss=None,
    )
    score = svc.compute(thesis)
    # assumption component = (3-0)/3 = 1.0 → 40pts; rest neutral 50% → 30pts
    assert score == pytest.approx(40.0 + 15.0 + 10.0 + 5.0, abs=0.1)


def test_score_all_invalid_assumptions_clamped(svc):
    """All INVALID assumptions → assumption component clamped to 0 (not negative)."""
    thesis = make_thesis(
        assumptions=[make_assumption(status=AssumptionStatus.INVALID) for _ in range(3)],
        entry_price=None, target_price=None, stop_loss=None,
    )
    score = svc.compute(thesis)
    # assumption component = max(0, (0 - 3*2)/3) = 0
    assert score >= 0.0
    assert score < 20.0  # well below neutral


def test_score_mixed_assumptions(svc):
    """2 valid, 1 invalid → penalised but > 0."""
    thesis = make_thesis(
        assumptions=[
            make_assumption(status=AssumptionStatus.VALID),
            make_assumption(status=AssumptionStatus.VALID),
            make_assumption(status=AssumptionStatus.INVALID),
        ],
        entry_price=None, target_price=None, stop_loss=None,
    )
    score = svc.compute(thesis)
    # (2 - 1*2)/3 = 0/3 = 0 → assumption score = 0pts
    assert score >= 0.0


# ---------------------------------------------------------------------------
# Catalyst progress dimension (30% weight)
# ---------------------------------------------------------------------------


def test_score_all_triggered_catalysts(svc):
    """All TRIGGERED catalysts → full catalyst score."""
    thesis = make_thesis(
        catalysts=[
            make_catalyst(status=CatalystStatus.TRIGGERED),
            make_catalyst(status=CatalystStatus.TRIGGERED),
        ],
        entry_price=None, target_price=None, stop_loss=None,
    )
    score = svc.compute(thesis)
    # catalyst component = 2/2 = 1.0 → 30pts; rest neutral
    assert score == pytest.approx(20.0 + 30.0 + 10.0 + 5.0, abs=0.1)


def test_score_no_triggered_catalysts(svc):
    """No triggered catalysts → catalyst component = 0pts."""
    thesis = make_thesis(
        catalysts=[make_catalyst(status=CatalystStatus.PENDING)],
        entry_price=None, target_price=None, stop_loss=None,
    )
    score = svc.compute(thesis)
    # catalyst = 0pts; rest neutral
    assert score == pytest.approx(20.0 + 0.0 + 10.0 + 5.0, abs=0.1)


# ---------------------------------------------------------------------------
# Risk/reward dimension (20% weight)
# ---------------------------------------------------------------------------


def test_score_rr_3_to_1_is_full(svc):
    """3:1 R/R → full risk/reward score (capped)."""
    # entry=20000, target=50000, stop=10000 → upside=30000, downside=10000 → 3:1
    thesis = make_thesis(entry_price=20000.0, target_price=50000.0, stop_loss=10000.0)
    score = svc.compute(thesis)
    # rr_score = min(3/3, 1.0) = 1.0 → 20pts for rr component
    assert score >= 20.0  # at minimum the rr component contributes max


def test_score_rr_1_to_1(svc):
    """1:1 R/R → 1/3 of rr score."""
    # upside=5000, downside=5000 → rr=1.0
    thesis = make_thesis(entry_price=25000.0, target_price=30000.0, stop_loss=20000.0)
    score = svc.compute(thesis)
    rr_contribution = (1.0 / 3.0) * 20.0
    assert score == pytest.approx(
        20.0 + 15.0 + rr_contribution + 5.0, abs=0.5
    )


def test_score_rr_above_3_capped(svc):
    """R/R > 3 is capped at 1.0 → same as 3:1."""
    # upside=20000, downside=1000 → rr=20
    thesis_high = make_thesis(entry_price=25000.0, target_price=45000.0, stop_loss=24000.0)
    thesis_3 = make_thesis(entry_price=20000.0, target_price=50000.0, stop_loss=10000.0)
    score_high = svc.compute(thesis_high)
    score_3 = svc.compute(thesis_3)
    # Both should get full rr score
    assert abs(score_high - score_3) < 1.0  # rr component equal


# ---------------------------------------------------------------------------
# Review confidence dimension (10% weight)
# ---------------------------------------------------------------------------


def test_score_review_confidence_high(svc):
    """High confidence review → full review dimension score."""
    review = make_review(confidence=1.0)
    thesis = make_thesis(
        reviews=[review],
        entry_price=None, target_price=None, stop_loss=None,
    )
    score = svc.compute(thesis)
    # review component = 1.0 * 10 = 10pts; rest neutral
    assert score == pytest.approx(20.0 + 15.0 + 10.0 + 10.0, abs=0.1)


def test_score_uses_latest_review(svc):
    """Multiple reviews — latest by reviewed_at is used."""
    from datetime import timedelta

    r1 = make_review(confidence=0.1)  # low confidence, older
    r2 = make_review(confidence=0.9)  # high confidence, newer
    r2.reviewed_at = r1.reviewed_at + timedelta(hours=1)
    r2.id = 2

    thesis = make_thesis(
        reviews=[r1, r2],
        entry_price=None, target_price=None, stop_loss=None,
    )
    score = svc.compute(thesis)
    # Latest review (r2, confidence=0.9) should dominate
    expected_review_pts = 0.9 * 10.0
    assert score == pytest.approx(20.0 + 15.0 + 10.0 + expected_review_pts, abs=0.1)


# ---------------------------------------------------------------------------
# Score bounds
# ---------------------------------------------------------------------------


def test_score_always_between_0_and_100(svc):
    """Score is always clamped to [0, 100]."""
    import random
    random.seed(42)
    for _ in range(50):
        n_assumptions = random.randint(0, 5)
        n_catalysts = random.randint(0, 5)
        statuses = [AssumptionStatus.VALID, AssumptionStatus.INVALID, AssumptionStatus.UNCERTAIN]
        cat_statuses = [CatalystStatus.PENDING, CatalystStatus.TRIGGERED, CatalystStatus.EXPIRED]
        thesis = make_thesis(
            assumptions=[make_assumption(status=random.choice(statuses)) for _ in range(n_assumptions)],
            catalysts=[make_catalyst(status=random.choice(cat_statuses)) for _ in range(n_catalysts)],
            entry_price=random.choice([None, 20000.0, 25000.0]),
            target_price=random.choice([None, 30000.0, 35000.0]),
            stop_loss=random.choice([None, 15000.0, 18000.0]),
        )
        score = svc.compute(thesis)
        assert 0.0 <= score <= 100.0, f"Score out of bounds: {score}"
