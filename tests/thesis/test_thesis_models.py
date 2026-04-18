"""Unit tests for Thesis domain model helpers (pure, no DB)."""

from __future__ import annotations

import pytest

from src.thesis.models import (
    Assumption,
    AssumptionStatus,
    Catalyst,
    CatalystStatus,
    Thesis,
    ThesisStatus,
)


def make_thesis(**kwargs) -> Thesis:
    from datetime import datetime, timezone

    defaults = dict(
        id=1,
        user_id="u1",
        ticker="HPG",
        title="HPG thesis",
        status=ThesisStatus.ACTIVE,
        entry_price=50_000.0,
        target_price=65_000.0,
        stop_loss=45_000.0,
        score=75.0,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
    )
    defaults.update(kwargs)
    t = Thesis.__new__(Thesis)
    for k, v in defaults.items():
        object.__setattr__(t, k, v)
    t.assumptions = []
    t.catalysts = []
    t.reviews = []
    t.snapshots = []
    return t


# ---------------------------------------------------------------------------
# is_active
# ---------------------------------------------------------------------------


def test_is_active_true():
    assert make_thesis(status=ThesisStatus.ACTIVE).is_active


def test_is_active_false_when_invalidated():
    assert not make_thesis(status=ThesisStatus.INVALIDATED).is_active


# ---------------------------------------------------------------------------
# upside_pct
# ---------------------------------------------------------------------------


def test_upside_pct_correct():
    t = make_thesis(entry_price=50_000.0, target_price=65_000.0)
    assert abs(t.upside_pct - 30.0) < 0.001


def test_upside_pct_none_when_no_entry():
    t = make_thesis(entry_price=None, target_price=65_000.0)
    assert t.upside_pct is None


def test_upside_pct_none_when_no_target():
    t = make_thesis(entry_price=50_000.0, target_price=None)
    assert t.upside_pct is None


# ---------------------------------------------------------------------------
# risk_reward
# ---------------------------------------------------------------------------


def test_risk_reward_correct():
    # upside = 15000, downside = 5000 → R/R = 3.0
    t = make_thesis(entry_price=50_000.0, target_price=65_000.0, stop_loss=45_000.0)
    assert abs(t.risk_reward - 3.0) < 0.001


def test_risk_reward_none_when_no_stop_loss():
    t = make_thesis(entry_price=50_000.0, target_price=65_000.0, stop_loss=None)
    assert t.risk_reward is None


def test_risk_reward_none_when_stop_above_entry():
    t = make_thesis(entry_price=50_000.0, target_price=65_000.0, stop_loss=55_000.0)
    assert t.risk_reward is None


# ---------------------------------------------------------------------------
# invalid_assumption_count
# ---------------------------------------------------------------------------


def test_invalid_assumption_count():
    t = make_thesis()
    a1 = Assumption.__new__(Assumption)
    a1.status = AssumptionStatus.INVALID
    a2 = Assumption.__new__(Assumption)
    a2.status = AssumptionStatus.VALID
    a3 = Assumption.__new__(Assumption)
    a3.status = AssumptionStatus.INVALID
    t.assumptions = [a1, a2, a3]
    assert t.invalid_assumption_count == 2


# ---------------------------------------------------------------------------
# triggered_catalyst_count
# ---------------------------------------------------------------------------


def test_triggered_catalyst_count():
    t = make_thesis()
    c1 = Catalyst.__new__(Catalyst)
    c1.status = CatalystStatus.TRIGGERED
    c2 = Catalyst.__new__(Catalyst)
    c2.status = CatalystStatus.PENDING
    t.catalysts = [c1, c2]
    assert t.triggered_catalyst_count == 1
