"""Unit tests for watchlist domain model helpers (no DB required)."""

import pytest

from src.watchlist.models import Alert, AlertConditionType, AlertStatus


def _make_alert(
    condition: AlertConditionType,
    threshold: float,
    status: AlertStatus = AlertStatus.ACTIVE,
) -> Alert:
    a = Alert.__new__(Alert)
    a.condition_type = condition
    a.threshold = threshold
    a.status = status
    return a


# ---- PRICE_ABOVE ----


def test_price_above_triggers() -> None:
    a = _make_alert(AlertConditionType.PRICE_ABOVE, threshold=90_000)
    assert a.is_triggered_by(current_price=90_000, change_pct=1.5, volume_ratio=1.0) is True


def test_price_above_no_trigger() -> None:
    a = _make_alert(AlertConditionType.PRICE_ABOVE, threshold=90_000)
    assert a.is_triggered_by(current_price=89_999, change_pct=1.5, volume_ratio=1.0) is False


# ---- PRICE_BELOW ----


def test_price_below_triggers() -> None:
    a = _make_alert(AlertConditionType.PRICE_BELOW, threshold=70_000)
    assert a.is_triggered_by(current_price=70_000, change_pct=-2.0, volume_ratio=1.0) is True


def test_price_below_no_trigger() -> None:
    a = _make_alert(AlertConditionType.PRICE_BELOW, threshold=70_000)
    assert a.is_triggered_by(current_price=70_001, change_pct=-1.0, volume_ratio=1.0) is False


# ---- CHANGE_PCT_UP ----


def test_change_pct_up_triggers() -> None:
    a = _make_alert(AlertConditionType.CHANGE_PCT_UP, threshold=5.0)
    assert a.is_triggered_by(current_price=0, change_pct=5.0, volume_ratio=1.0) is True


def test_change_pct_up_no_trigger() -> None:
    a = _make_alert(AlertConditionType.CHANGE_PCT_UP, threshold=5.0)
    assert a.is_triggered_by(current_price=0, change_pct=4.99, volume_ratio=1.0) is False


# ---- CHANGE_PCT_DOWN ----


def test_change_pct_down_triggers() -> None:
    a = _make_alert(AlertConditionType.CHANGE_PCT_DOWN, threshold=3.0)
    assert a.is_triggered_by(current_price=0, change_pct=-3.0, volume_ratio=1.0) is True


def test_change_pct_down_no_trigger() -> None:
    a = _make_alert(AlertConditionType.CHANGE_PCT_DOWN, threshold=3.0)
    assert a.is_triggered_by(current_price=0, change_pct=-2.99, volume_ratio=1.0) is False


# ---- VOLUME_SPIKE ----


def test_volume_spike_triggers() -> None:
    a = _make_alert(AlertConditionType.VOLUME_SPIKE, threshold=2.0)
    assert a.is_triggered_by(current_price=0, change_pct=0, volume_ratio=2.0) is True


def test_volume_spike_no_trigger() -> None:
    a = _make_alert(AlertConditionType.VOLUME_SPIKE, threshold=2.0)
    assert a.is_triggered_by(current_price=0, change_pct=0, volume_ratio=1.99) is False


# ---- Non-active alert never triggers ----


def test_triggered_alert_does_not_re_trigger() -> None:
    a = _make_alert(
        AlertConditionType.PRICE_ABOVE,
        threshold=50_000,
        status=AlertStatus.TRIGGERED,
    )
    assert a.is_triggered_by(current_price=99_999, change_pct=5.0, volume_ratio=5.0) is False


def test_dismissed_alert_does_not_trigger() -> None:
    a = _make_alert(
        AlertConditionType.PRICE_ABOVE,
        threshold=50_000,
        status=AlertStatus.DISMISSED,
    )
    assert a.is_triggered_by(current_price=99_999, change_pct=5.0, volume_ratio=5.0) is False
