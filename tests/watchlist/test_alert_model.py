"""Unit tests for Alert domain helpers (pure logic, no DB)."""
from __future__ import annotations

from src.watchlist.models import Alert, AlertConditionType, AlertStatus


def _alert(condition: AlertConditionType, threshold: float) -> Alert:
    return Alert(
        user_id="u1",
        ticker="VNM",
        condition_type=condition,
        threshold=threshold,
        status=AlertStatus.ACTIVE,
    )


def test_price_above_fires():
    a = _alert(AlertConditionType.PRICE_ABOVE, 50_000)
    assert a.is_triggered_by(current_price=55_000, change_pct=0, volume_ratio=1) is True


def test_price_above_does_not_fire_below():
    a = _alert(AlertConditionType.PRICE_ABOVE, 50_000)
    assert a.is_triggered_by(current_price=49_999, change_pct=0, volume_ratio=1) is False


def test_price_below_fires():
    a = _alert(AlertConditionType.PRICE_BELOW, 30_000)
    assert a.is_triggered_by(current_price=29_000, change_pct=0, volume_ratio=1) is True


def test_change_pct_up_fires():
    a = _alert(AlertConditionType.CHANGE_PCT_UP, 5.0)
    assert a.is_triggered_by(current_price=0, change_pct=6.5, volume_ratio=1) is True


def test_change_pct_down_fires():
    a = _alert(AlertConditionType.CHANGE_PCT_DOWN, 5.0)
    assert a.is_triggered_by(current_price=0, change_pct=-6.0, volume_ratio=1) is True


def test_change_pct_down_does_not_fire_on_positive():
    a = _alert(AlertConditionType.CHANGE_PCT_DOWN, 5.0)
    assert a.is_triggered_by(current_price=0, change_pct=6.0, volume_ratio=1) is False


def test_volume_spike_fires():
    a = _alert(AlertConditionType.VOLUME_SPIKE, 2.0)
    assert a.is_triggered_by(current_price=0, change_pct=0, volume_ratio=2.5) is True


def test_triggered_alert_does_not_re_fire():
    a = _alert(AlertConditionType.PRICE_ABOVE, 50_000)
    a.mark_triggered(price=55_000)
    assert a.status == AlertStatus.TRIGGERED
    # Even if price still above threshold, already triggered
    assert a.is_triggered_by(current_price=60_000, change_pct=0, volume_ratio=1) is False


def test_mark_triggered_sets_price():
    a = _alert(AlertConditionType.PRICE_ABOVE, 50_000)
    a.mark_triggered(price=55_500)
    assert a.triggered_price == 55_500
    assert a.triggered_at is not None


def test_mark_triggered_idempotent():
    a = _alert(AlertConditionType.PRICE_ABOVE, 50_000)
    a.mark_triggered(price=55_000)
    first_time = a.triggered_at
    a.mark_triggered(price=60_000)  # second call — should not change state
    assert a.triggered_price == 55_000
    assert a.triggered_at == first_time
