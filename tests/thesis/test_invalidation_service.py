"""Unit tests for InvalidationService."""
from unittest.mock import MagicMock

import pytest

from src.thesis.models import Assumption, AssumptionStatus
from src.thesis.invalidation_service import InvalidationService


def _make_thesis_with_assumptions(statuses: list[AssumptionStatus]):
    t = MagicMock()
    t.id = 1
    t.assumptions = []
    for s in statuses:
        a = MagicMock(spec=Assumption)
        a.status = s
        a.description = f"Assumption with status {s}"
        t.assumptions.append(a)
    return t


def test_no_invalidation_when_assumptions_healthy() -> None:
    t = _make_thesis_with_assumptions([
        AssumptionStatus.VALID,
        AssumptionStatus.VALID,
        AssumptionStatus.PENDING,
    ])
    svc = InvalidationService()
    result = svc.check(t, current_score=65.0)
    assert result.should_invalidate is False


def test_invalidation_when_majority_invalid() -> None:
    t = _make_thesis_with_assumptions([
        AssumptionStatus.INVALID,
        AssumptionStatus.INVALID,
        AssumptionStatus.VALID,
    ])
    svc = InvalidationService()
    result = svc.check(t, current_score=25.0)
    assert result.should_invalidate is True
    assert len(result.invalid_assumptions) == 2


def test_no_invalidation_with_no_assumptions() -> None:
    t = _make_thesis_with_assumptions([])
    svc = InvalidationService()
    result = svc.check(t, current_score=50.0)
    assert result.should_invalidate is False


def test_score_carried_through() -> None:
    t = _make_thesis_with_assumptions([AssumptionStatus.VALID])
    svc = InvalidationService()
    result = svc.check(t, current_score=77.5)
    assert result.score == 77.5
