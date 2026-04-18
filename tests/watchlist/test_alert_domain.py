"""Unit tests for Alert domain helpers: is_triggered_by() and mark_triggered().

Pure domain logic — no DB, no async, no mocks.
All 5 AlertConditionType variants tested.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.watchlist.models import Alert, AlertConditionType, AlertStatus

from tests.watchlist.conftest import make_alert


# ---------------------------------------------------------------------------
# is_triggered_by — PRICE_ABOVE
# ---------------------------------------------------------------------------


def test_price_above_triggered_at_threshold():
    alert = make_alert(condition_type=AlertConditionType.PRICE_ABOVE, threshold=30000.0)
    assert alert.is_triggered_by(30000.0, 0.0, 1.0) is True


def test_price_above_triggered_over_threshold():
    alert = make_alert(condition_type=AlertConditionType.PRICE_ABOVE, threshold=30000.0)
    assert alert.is_triggered_by(35000.0, 0.0, 1.0) is True


def test_price_above_not_triggered_below():
    alert = make_alert(condition_type=AlertConditionType.PRICE_ABOVE, threshold=30000.0)
    assert alert.is_triggered_by(29999.0, 0.0, 1.0) is False


# ---------------------------------------------------------------------------
# is_triggered_by — PRICE_BELOW
# ---------------------------------------------------------------------------


def test_price_below_triggered_at_threshold():
    alert = make_alert(condition_type=AlertConditionType.PRICE_BELOW, threshold=20000.0)
    assert alert.is_triggered_by(20000.0, 0.0, 1.0) is True


def test_price_below_triggered_under_threshold():
    alert = make_alert(condition_type=AlertConditionType.PRICE_BELOW, threshold=20000.0)
    assert alert.is_triggered_by(15000.0, 0.0, 1.0) is True


def test_price_below_not_triggered_above():
    alert = make_alert(condition_type=AlertConditionType.PRICE_BELOW, threshold=20000.0)
    assert alert.is_triggered_by(20001.0, 0.0, 1.0) is False


# ---------------------------------------------------------------------------
# is_triggered_by — CHANGE_PCT_UP
# ---------------------------------------------------------------------------


def test_change_pct_up_triggered():
    alert = make_alert(condition_type=AlertConditionType.CHANGE_PCT_UP, threshold=5.0)
    assert alert.is_triggered_by(0.0, 5.0, 1.0) is True


def test_change_pct_up_triggered_above():
    alert = make_alert(condition_type=AlertConditionType.CHANGE_PCT_UP, threshold=5.0)
    assert alert.is_triggered_by(0.0, 7.3, 1.0) is True


def test_change_pct_up_not_triggered_below():
    alert = make_alert(condition_type=AlertConditionType.CHANGE_PCT_UP, threshold=5.0)
    assert alert.is_triggered_by(0.0, 4.9, 1.0) is False


# ---------------------------------------------------------------------------
# is_triggered_by — CHANGE_PCT_DOWN
# ---------------------------------------------------------------------------


def test_change_pct_down_triggered():
    """threshold=5 means price fell -5% or more."""
    alert = make_alert(condition_type=AlertConditionType.CHANGE_PCT_DOWN, threshold=5.0)
    assert alert.is_triggered_by(0.0, -5.0, 1.0) is True


def test_change_pct_down_triggered_beyond():
    alert = make_alert(condition_type=AlertConditionType.CHANGE_PCT_DOWN, threshold=5.0)
    assert alert.is_triggered_by(0.0, -7.0, 1.0) is True


def test_change_pct_down_not_triggered_small_drop():
    alert = make_alert(condition_type=AlertConditionType.CHANGE_PCT_DOWN, threshold=5.0)
    assert alert.is_triggered_by(0.0, -4.9, 1.0) is False


def test_change_pct_down_not_triggered_positive():
    alert = make_alert(condition_type=AlertConditionType.CHANGE_PCT_DOWN, threshold=5.0)
    assert alert.is_triggered_by(0.0, 2.0, 1.0) is False


# ---------------------------------------------------------------------------
# is_triggered_by — VOLUME_SPIKE
# ---------------------------------------------------------------------------


def test_volume_spike_triggered():
    alert = make_alert(condition_type=AlertConditionType.VOLUME_SPIKE, threshold=2.0)
    assert alert.is_triggered_by(0.0, 0.0, 2.0) is True


def test_volume_spike_not_triggered_below():
    alert = make_alert(condition_type=AlertConditionType.VOLUME_SPIKE, threshold=2.0)
    assert alert.is_triggered_by(0.0, 0.0, 1.9) is False


# ---------------------------------------------------------------------------
# is_triggered_by — non-ACTIVE status never triggers
# ---------------------------------------------------------------------------


def test_triggered_alert_does_not_re_trigger():
    alert = make_alert(
        condition_type=AlertConditionType.PRICE_ABOVE,
        threshold=30000.0,
        status=AlertStatus.TRIGGERED,
    )
    assert alert.is_triggered_by(99999.0, 99.0, 99.0) is False


def test_dismissed_alert_does_not_trigger():
    alert = make_alert(
        condition_type=AlertConditionType.PRICE_ABOVE,
        threshold=30000.0,
        status=AlertStatus.DISMISSED,
    )
    assert alert.is_triggered_by(99999.0, 99.0, 99.0) is False


def test_expired_alert_does_not_trigger():
    alert = make_alert(
        condition_type=AlertConditionType.PRICE_ABOVE,
        threshold=30000.0,
        status=AlertStatus.EXPIRED,
    )
    assert alert.is_triggered_by(99999.0, 99.0, 99.0) is False


# ---------------------------------------------------------------------------
# mark_triggered
# ---------------------------------------------------------------------------


def test_mark_triggered_transitions_status():
    alert = make_alert(status=AlertStatus.ACTIVE)
    alert.mark_triggered()
    assert alert.status == AlertStatus.TRIGGERED
    assert alert.triggered_at is not None


def test_mark_triggered_stores_price():
    alert = make_alert(status=AlertStatus.ACTIVE)
    alert.mark_triggered(price=28500.0)
    assert alert.triggered_price == 28500.0


def test_mark_triggered_idempotent():
    """Calling mark_triggered twice must not change triggered_at."""
    alert = make_alert(status=AlertStatus.ACTIVE)
    alert.mark_triggered()
    first_triggered_at = alert.triggered_at
    alert.mark_triggered()  # second call — should be no-op
    assert alert.triggered_at == first_triggered_at
    assert alert.status == AlertStatus.TRIGGERED


def test_mark_triggered_without_price_leaves_triggered_price_none():
    alert = make_alert(status=AlertStatus.ACTIVE)
    alert.mark_triggered()
    assert alert.triggered_price is None
