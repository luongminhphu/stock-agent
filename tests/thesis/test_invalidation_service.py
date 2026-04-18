"""Unit tests for InvalidationService.

Pure synchronous service. Tests all invalidation rules and edge cases.
"""

from __future__ import annotations

import pytest

from src.thesis.invalidation_service import (
    InvalidationService,
    _MAX_INVALID_ASSUMPTION_RATIO,
)
from src.thesis.models import AssumptionStatus

from tests.thesis.conftest import make_assumption, make_thesis


@pytest.fixture()
def svc() -> InvalidationService:
    return InvalidationService()


# ---------------------------------------------------------------------------
# Should NOT invalidate
# ---------------------------------------------------------------------------


def test_no_invalidation_all_valid_assumptions(svc):
    thesis = make_thesis(
        assumptions=[
            make_assumption(status=AssumptionStatus.VALID),
            make_assumption(status=AssumptionStatus.VALID),
            make_assumption(status=AssumptionStatus.VALID),
        ]
    )
    result = svc.check(thesis, current_score=75.0)
    assert result.should_invalidate is False
    assert result.invalid_assumptions == []


def test_no_invalidation_below_threshold(svc):
    """Exactly AT threshold (50%) should NOT trigger (rule is strictly >)."""
    thesis = make_thesis(
        assumptions=[
            make_assumption("A1", AssumptionStatus.INVALID),
            make_assumption("A2", AssumptionStatus.VALID),
        ]
    )
    # ratio = 1/2 = 0.50 — NOT > 0.50 → should not invalidate
    result = svc.check(thesis, current_score=30.0)
    assert result.should_invalidate is False


def test_no_invalidation_empty_assumptions(svc):
    """No assumptions at all → cannot trigger assumption-based invalidation."""
    thesis = make_thesis(assumptions=[])
    result = svc.check(thesis, current_score=50.0)
    assert result.should_invalidate is False


def test_no_invalidation_mixed_but_below_threshold(svc):
    """1 invalid out of 3 = 33% < 50% → no invalidation."""
    thesis = make_thesis(
        assumptions=[
            make_assumption("A1", AssumptionStatus.VALID),
            make_assumption("A2", AssumptionStatus.VALID),
            make_assumption("A3", AssumptionStatus.INVALID),
        ]
    )
    result = svc.check(thesis, current_score=40.0)
    assert result.should_invalidate is False
    assert len(result.invalid_assumptions) == 1


# ---------------------------------------------------------------------------
# Should invalidate
# ---------------------------------------------------------------------------


def test_invalidation_majority_invalid(svc):
    """2 of 3 invalid = 67% > 50% → should invalidate."""
    thesis = make_thesis(
        assumptions=[
            make_assumption("A1", AssumptionStatus.INVALID),
            make_assumption("A2", AssumptionStatus.INVALID),
            make_assumption("A3", AssumptionStatus.VALID),
        ]
    )
    result = svc.check(thesis, current_score=15.0)
    assert result.should_invalidate is True
    assert len(result.invalid_assumptions) == 2
    assert "A1" in result.invalid_assumptions
    assert "A2" in result.invalid_assumptions


def test_invalidation_all_invalid(svc):
    """All assumptions invalid → definitely invalidate."""
    thesis = make_thesis(
        assumptions=[
            make_assumption("A1", AssumptionStatus.INVALID),
            make_assumption("A2", AssumptionStatus.INVALID),
        ]
    )
    result = svc.check(thesis, current_score=5.0)
    assert result.should_invalidate is True
    assert result.score == 5.0


def test_invalidation_reason_contains_counts(svc):
    """Invalidation reason should mention counts for human readability."""
    thesis = make_thesis(
        assumptions=[
            make_assumption("A1", AssumptionStatus.INVALID),
            make_assumption("A2", AssumptionStatus.INVALID),
            make_assumption("A3", AssumptionStatus.VALID),
        ]
    )
    result = svc.check(thesis, current_score=10.0)
    assert "2" in result.reason
    assert "3" in result.reason


# ---------------------------------------------------------------------------
# Result fields
# ---------------------------------------------------------------------------


def test_result_score_is_passed_through(svc):
    """current_score is reflected unchanged in the result."""
    thesis = make_thesis(assumptions=[])
    result = svc.check(thesis, current_score=42.5)
    assert result.score == 42.5


def test_result_invalid_assumptions_list_descriptions(svc):
    """invalid_assumptions list contains description strings, not objects."""
    thesis = make_thesis(
        assumptions=[
            make_assumption("Steel demand", AssumptionStatus.INVALID),
            make_assumption("Export quota", AssumptionStatus.VALID),
        ]
    )
    result = svc.check(thesis, current_score=60.0)
    assert isinstance(result.invalid_assumptions, list)
    assert all(isinstance(s, str) for s in result.invalid_assumptions)
    assert "Steel demand" in result.invalid_assumptions
    assert "Export quota" not in result.invalid_assumptions


def test_threshold_constant_is_half(svc):
    """Validate threshold constant hasn't been accidentally changed."""
    assert _MAX_INVALID_ASSUMPTION_RATIO == 0.5
