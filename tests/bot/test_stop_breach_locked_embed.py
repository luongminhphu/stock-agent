"""Wave 9.4: stop-breach embed messaging cho vị thế bị khóa (ESOP).

Khi thesis bị invalidate nhưng vị thế bị khóa hoàn toàn (sellable_qty == 0),
khuyến nghị exit_signal lặp lại mỗi ngày là nhiễu — embed phải chuyển sang
message "vị thế khóa — đặt nhắc xem lại khi mở khóa".
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

from src.bot.commands.thesis_embeds import build_stop_breach_embed

_NOW = datetime.datetime(2026, 9, 18, 8, 0, tzinfo=datetime.UTC)


def _outcome(**over):
    base = dict(
        action="invalidated",
        thesis_id=42,
        ticker="HPG",
        current_price=21700.0,
        stop_loss=23000.0,
        overshoot_pct=5.7,
        ai_verdict="CONFIRMED",
        ai_confidence=0.85,
        ai_action="exit_signal",
        reason="stop_loss_breached",
        locked_qty=0.0,
        sellable_qty=None,
        locked_reason=None,
        locked_until=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _field0(outcomes):
    embed = build_stop_breach_embed(outcomes, _NOW)
    return embed.fields[0].value


class TestFullyLockedPosition:
    def test_no_exit_signal_when_fully_locked(self):
        """Sellable == 0 → không render exit_signal, có nhắc xem lại mở khóa."""
        val = _field0([_outcome(
            locked_qty=5000.0, sellable_qty=0.0,
            locked_reason="esop", locked_until=datetime.date(2026, 12, 15),
        )])
        assert "vô hiệu" in val
        assert "Không thể bán" in val
        assert "15/12/2026" in val
        assert "exit_signal" not in val

    def test_partially_locked_keeps_exit_signal(self):
        """Còn phần bán được (sellable > 0) → giữ nguyên flow cũ."""
        val = _field0([_outcome(
            locked_qty=2000.0, sellable_qty=3000.0, locked_reason="esop",
        )])
        assert "exit_signal" in val
        assert "INVALIDATE" in val

    def test_old_outcome_shape_backward_compat(self):
        """Outcome cũ (không có attrs locked_*) vẫn render flow cũ — getattr fallback."""
        old = SimpleNamespace(
            action="invalidated", thesis_id=1, ticker="VNM",
            current_price=60000.0, stop_loss=65000.0, overshoot_pct=7.7,
            ai_verdict="CONFIRMED", ai_confidence=0.9, ai_action="exit_signal",
            reason="stop_loss_breached",
        )
        val = _field0([old])
        assert "exit_signal" in val


class TestUnlockDateFormat:
    def test_datetime_until_converted_to_date(self):
        val = _field0([_outcome(
            locked_qty=1000.0, sellable_qty=0.0,
            locked_until=datetime.datetime(2027, 3, 1, 12, 0, tzinfo=datetime.UTC),
        )])
        assert "01/03/2027" in val

    def test_no_until_no_date(self):
        val = _field0([_outcome(locked_qty=1000.0, sellable_qty=0.0)])
        assert "mở khóa dự kiến" not in val
