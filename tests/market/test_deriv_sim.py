"""market.deriv_sim — engine giả lập HĐTL VN30 theo base rate."""

from __future__ import annotations

from datetime import date

from src.market.deriv_sim import (
    SimParams,
    SimPosition,
    is_trading_slot,
    stats,
    step,
    trade_pnl,
)


def _c(series: str, code: str, price: float | None, dte: int) -> dict:
    return {"series": series, "code": code, "price": price, "days_to_expiry": dte}


CONTRACTS = [
    _c("F1M", "VN30F2611", 1380.0, 8),
    _c("F2M", "VN30F2612", 1376.0, 36),
    _c("F1Q", "VN30F2703", 1371.0, 127),
    _c("F2Q", "VN30F2706", None, 218),
]
P = SimParams(series="F1M", qty=2, stop_points=15, target_points=30, fee_per_side=0)


def test_opens_side_from_signal_and_stays_flat_on_neutral() -> None:
    r = step(None, "NGHIENG_MUA", CONTRACTS, P)
    assert [(a.kind, a.code, a.side, a.qty, a.price) for a in r.actions] == [
        ("open", "VN30F2611", "LONG", 2, 1380.0)
    ]
    assert step(None, "NGHIENG_TRANH", CONTRACTS, P).actions[0].side == "SHORT"
    assert step(None, "TRUNG_TINH", CONTRACTS, P).actions == []
    assert step(None, "KHONG_DU_TIEN_LE", CONTRACTS, P).actions == []


def test_stop_and_target_close_and_block_reentry() -> None:
    long_pos = SimPosition("VN30F2611", "LONG", 2, 1400.0)  # mark 1380 → -20 điểm
    r = step(long_pos, "NGHIENG_MUA", CONTRACTS, P)
    assert [(a.kind, a.reason) for a in r.actions] == [("close", "stop")] and r.block_reentry
    short_pos = SimPosition("VN30F2611", "SHORT", 2, 1415.0)  # +35 điểm
    r = step(short_pos, "NGHIENG_TRANH", CONTRACTS, P)
    assert [(a.kind, a.reason) for a in r.actions] == [("close", "target")]
    # Bị chặn vào lại trong phiên dù tín hiệu vẫn còn
    assert step(None, "NGHIENG_MUA", CONTRACTS, P, reentry_blocked=True).actions == []


def test_signal_flip_closes_and_reverses() -> None:
    pos = SimPosition("VN30F2611", "LONG", 2, 1378.0)
    r = step(pos, "NGHIENG_TRANH", CONTRACTS, P)
    assert [(a.kind, a.side, a.reason) for a in r.actions] == [
        ("close", "LONG", "signal"),
        ("open", "SHORT", "signal"),
    ]
    r = step(pos, "TRUNG_TINH", CONTRACTS, P)  # về Neutral → chỉ đóng
    assert [(a.kind, a.reason) for a in r.actions] == [("close", "signal")]
    r = step(pos, "NGHIENG_MUA", CONTRACTS, P)  # vẫn cùng chiều → giữ
    assert r.actions == [] and r.mark == 1380.0


def test_roll_near_expiry_keeps_side() -> None:
    near = [_c("F1M", "VN30F2611", 1380.0, 2), *CONTRACTS[1:]]
    pos = SimPosition("VN30F2611", "SHORT", 1, 1382.0)
    r = step(pos, "NGHIENG_TRANH", near, P)
    assert [(a.kind, a.code, a.side, a.reason) for a in r.actions] == [
        ("close", "VN30F2611", "SHORT", "roll"),
        ("open", "VN30F2612", "SHORT", "roll"),
    ]
    # Mở mới khi F1M đã sát đáo hạn → dùng F2M
    assert step(None, "NGHIENG_MUA", near, P).actions[0].code == "VN30F2612"


def test_no_price_means_no_action() -> None:
    no_px = [_c("F1M", "VN30F2611", None, 8)]
    assert step(SimPosition("VN30F2611", "LONG", 1, 1380.0), "TRUNG_TINH", no_px, P).actions == []
    assert step(None, "NGHIENG_MUA", no_px, P).actions == []


def test_trade_pnl_includes_fees() -> None:
    assert trade_pnl("LONG", 2, 1380.0, 1390.0, 0) == 2_000_000
    assert trade_pnl("SHORT", 1, 1380.0, 1390.0, 5_000) == -1_010_000


def test_stats_win_rate_profit_factor_drawdown() -> None:
    trades = [
        {"side": "LONG", "realized_pnl": 3_000_000, "exit_reason": "target"},
        {"side": "LONG", "realized_pnl": -1_500_000, "exit_reason": "stop"},
        {"side": "SHORT", "realized_pnl": -500_000, "exit_reason": "signal"},
        {"side": "SHORT", "realized_pnl": 1_000_000, "exit_reason": "signal"},
        {"side": "LONG", "realized_pnl": None},  # còn mở → không tính
    ]
    s = stats(trades)
    assert s["trades"] == 4 and s["wins"] == 2 and s["losses"] == 2
    assert s["win_rate"] == 0.5 and s["win_rate_long"] == 0.5 and s["win_rate_short"] == 0.5
    assert s["avg_win"] == 2_000_000 and s["avg_loss"] == -1_000_000
    assert s["profit_factor"] == 2.0 and s["total_pnl"] == 2_000_000
    assert s["max_drawdown"] == -2_000_000
    assert s["by_reason"] == {"signal": 2, "stop": 1, "target": 1, "roll": 0}
    assert stats([])["win_rate"] is None


def test_trading_slots() -> None:
    def always(d: date) -> bool:
        return True

    d = date(2026, 11, 12)
    assert is_trading_slot(d, 845, always) and is_trading_slot(d, 1445, always)
    assert not is_trading_slot(d, 1200, always) and not is_trading_slot(d, 1500, always)
    assert not is_trading_slot(d, 1000, lambda _d: False)


def test_levels_avoid_round_numbers_and_atr() -> None:
    from src.market.deriv_sim import levels

    p = SimParams(stop_points=15, target_points=30)
    assert levels("LONG", 1380.0, p) == (1363.5, 1408.5)
    assert levels("SHORT", 1380.0, p) == (1396.5, 1351.5)
    assert levels("LONG", 1382.0, p) == (1367.0, 1412.0)  # không sát số tròn → giữ
    off = SimParams(stop_points=15, target_points=None, avoid_round=False)
    assert levels("LONG", 1380.0, off) == (1365.0, None)
    atr_p = SimParams(stop_atr=0.5, target_atr=1.0, avoid_round=False)
    assert levels("LONG", 1382.0, atr_p, atr=20.0) == (1372.0, 1402.0)
    assert levels("LONG", 1382.0, atr_p, atr=None) == (1367.0, 1412.0)  # thiếu ATR → điểm


def test_fade_book_and_crowd_filter() -> None:
    r = step(None, "NGHIENG_MUA", CONTRACTS, P, fade=True)
    assert r.actions[0].side == "SHORT"
    chased = step(None, "NGHIENG_MUA", CONTRACTS, P, atr=20.0, move=17.0)
    assert chased.actions == [] and chased.skipped == "crowd"
    assert step(None, "NGHIENG_MUA", CONTRACTS, P, atr=20.0, move=-17.0).actions  # ngược chiều ok
    assert step(None, "NGHIENG_MUA", CONTRACTS, P, fade=True, atr=20.0, move=17.0).actions


def test_choose_primary_auto_needs_evidence() -> None:
    from src.market.deriv_sim import choose_primary

    base = {"trades": 25, "win_rate": 0.40, "total_pnl": -1_000_000}
    good_fade = {"trades": 25, "win_rate": 0.55, "total_pnl": 2_000_000}
    assert choose_primary("follow", base, good_fade) == "base"
    assert choose_primary("fade", base, {}) == "fade"
    assert choose_primary("auto", base, good_fade) == "fade"
    assert choose_primary("auto", {**base, "trades": 10}, good_fade) == "base"  # chưa đủ mẫu
    assert choose_primary("auto", base, {**good_fade, "win_rate": 0.45}) == "base"  # chưa rõ


def test_atr() -> None:
    from types import SimpleNamespace

    from src.market.deriv_sim import atr

    bars = [SimpleNamespace(high=105.0, low=95.0, close=100.0) for _ in range(15)]
    assert atr(bars) == 10.0 and atr(bars[:5]) is None
