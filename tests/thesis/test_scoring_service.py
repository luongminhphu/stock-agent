"""Unit tests for ScoringService (no DB required)."""
import pytest
from unittest.mock import MagicMock
from datetime import datetime

from src.thesis.models import (
    Assumption,
    AssumptionStatus,
    Catalyst,
    CatalystStatus,
    ThesisReview,
    ReviewVerdict,
    ThesisStatus,
)
from src.thesis.scoring_service import ScoringService


def _make_thesis_mock(entry=80_000, target=100_000, stop=72_000):
    t = MagicMock()
    t.id = 1
    t.status = ThesisStatus.ACTIVE
    t.entry_price = entry
    t.target_price = target
    t.stop_loss = stop
    t.assumptions = []
    t.catalysts = []
    t.reviews = []

    # Patch risk_reward property
    if entry and target and stop and entry > stop:
        type(t).risk_reward = property(lambda self: (target - entry) / (entry - stop))
    else:
        type(t).risk_reward = property(lambda self: None)

    return t


def test_score_all_valid_assumptions() -> None:
    t = _make_thesis_mock()
    for _ in range(3):
        a = MagicMock(spec=Assumption)
        a.status = AssumptionStatus.VALID
        t.assumptions.append(a)
    svc = ScoringService()
    score = svc.compute(t)
    assert score > 50  # should be healthy


def test_score_all_invalid_assumptions() -> None:
    t = _make_thesis_mock()
    for _ in range(3):
        a = MagicMock(spec=Assumption)
        a.status = AssumptionStatus.INVALID
        t.assumptions.append(a)
    svc = ScoringService()
    score = svc.compute(t)
    assert score < 30  # should be penalised hard


def test_score_bounds() -> None:
    t = _make_thesis_mock()
    svc = ScoringService()
    score = svc.compute(t)
    assert 0.0 <= score <= 100.0


def test_score_with_high_rr() -> None:
    # 3:1 R/R should get full risk_reward marks
    t = _make_thesis_mock(entry=80_000, target=104_000, stop=72_000)
    svc = ScoringService()
    score = svc.compute(t)
    assert score > 40
