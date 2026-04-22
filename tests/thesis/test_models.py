"""Unit tests for thesis domain model helpers (no DB required)."""

from unittest.mock import MagicMock

import pytest

from src.thesis.models import (
    Assumption,
    AssumptionStatus,
    Catalyst,
    CatalystStatus,
    Thesis,
    ThesisStatus,
)


def _make_thesis(
    entry: float = 80_000,
    target: float = 100_000,
    stop: float = 72_000,
) -> Thesis:
    t = Thesis.__new__(Thesis)
    t.id = 1
    t.user_id = "user123"
    t.ticker = "VNM"
    t.title = "VNM recovery play"
    t.summary = ""
    t.status = ThesisStatus.ACTIVE
    t.entry_price = entry
    t.target_price = target
    t.stop_loss = stop
    t.score = None
    t.assumptions = []
    t.catalysts = []
    t.reviews = []
    t.snapshots = []
    return t


def test_is_active() -> None:
    t = _make_thesis()
    assert t.is_active is True
    t.status = ThesisStatus.CLOSED
    assert t.is_active is False


def test_upside_pct() -> None:
    t = _make_thesis(entry=80_000, target=100_000)
    assert t.upside_pct == pytest.approx(25.0)


def test_upside_pct_none_when_missing_prices() -> None:
    t = _make_thesis()
    t.entry_price = None
    assert t.upside_pct is None


def test_risk_reward() -> None:
    t = _make_thesis(entry=80_000, target=100_000, stop=72_000)
    # upside = 20k, downside = 8k, R/R = 2.5
    assert t.risk_reward == pytest.approx(2.5)


def test_invalid_assumption_count() -> None:
    t = _make_thesis()
    a1 = MagicMock(spec=Assumption)
    a1.status = AssumptionStatus.INVALID
    a2 = MagicMock(spec=Assumption)
    a2.status = AssumptionStatus.VALID
    t.assumptions = [a1, a2]
    assert t.invalid_assumption_count == 1


def test_triggered_catalyst_count() -> None:
    t = _make_thesis()
    c1 = MagicMock(spec=Catalyst)
    c1.status = CatalystStatus.TRIGGERED
    c2 = MagicMock(spec=Catalyst)
    c2.status = CatalystStatus.PENDING
    t.catalysts = [c1, c2]
    assert t.triggered_catalyst_count == 1
