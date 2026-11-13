"""portfolio.deriv_sim_service — giả lập HĐTL VN30 lưu DB, quét định kỳ."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest

from src.portfolio.deriv_sim_service import DerivSimError, DerivSimService

TODAY = date(2026, 11, 12)
T0 = datetime(2026, 11, 12, 2, 0, tzinfo=UTC)
USER = "u1"


@dataclass
class _Rep:
    suggestion: str | None


class FakeDeriv:
    def __init__(self) -> None:
        self.price: float | None = 1380.0
        self.suggestion: str | None = "NGHIENG_MUA"

    async def contracts(self, today: date) -> dict:
        return {
            "contracts": [
                {"series": "F1M", "code": "VN30F2611", "price": self.price, "days_to_expiry": 8},
                {"series": "F2M", "code": "VN30F2612", "price": 1376.0, "days_to_expiry": 36},
            ]
        }

    async def vn30_baserate(self) -> _Rep:
        return _Rep(self.suggestion)


async def test_inactive_does_nothing(session) -> None:  # type: ignore[no-untyped-def]
    svc = DerivSimService(session, FakeDeriv())
    assert await svc.run_tick(USER, T0, TODAY) == []
    v = await svc.view(USER, TODAY)
    assert v["config"]["active"] is False and v["open"] is None and v["ticks"] == []


async def test_config_validation(session) -> None:  # type: ignore[no-untyped-def]
    svc = DerivSimService(session, FakeDeriv())
    with pytest.raises(DerivSimError):
        await svc.update_config(USER, series="F9X")
    with pytest.raises(DerivSimError):
        await svc.update_config(USER, qty=0)
    cfg = await svc.update_config(USER, stop_points=-10, clear_target=True)
    assert cfg.stop_points == 10 and cfg.target_points is None


def _p(events):  # type: ignore[no-untyped-def]
    return [e for e in events if e.primary]


async def test_open_stop_block_and_stats(session) -> None:  # type: ignore[no-untyped-def]
    d = FakeDeriv()
    svc = DerivSimService(session, d)
    await svc.update_config(USER, active=True, qty=2, stop_points=15, target_points=30, now=T0)

    ev = await svc.run_tick(USER, T0, TODAY)
    assert [(e.kind, e.strategy, e.side, e.primary) for e in ev] == [
        ("open", "base", "LONG", True),
        ("open", "fade", "SHORT", False),  # sổ Fade đánh ngược chạy song song
    ]
    # Stop 1380-15 = 1365 là số tròn → đẩy ra xa: 1363.5; Target 1410 → kéo về 1408.5
    assert (ev[0].stop_price, ev[0].target_price) == (1363.5, 1408.5)
    assert (ev[1].stop_price, ev[1].target_price) == (1396.5, 1351.5)
    d.price = 1385.0
    assert await svc.run_tick(USER, T0 + timedelta(minutes=5), TODAY) == []
    v = await svc.view(USER, TODAY)
    # +5 điểm × 100.000 × 2 HĐ − phí 2 chiều (5.000 × 2 × 2)
    assert v["open"]["mark_price"] == 1385.0 and v["open"]["unrealized_pnl"] == 980_000

    d.price = 1364.0  # chạm 1365 (vùng tròn) nhưng chưa thủng 1363.5 → chưa Stop
    assert await svc.run_tick(USER, T0 + timedelta(minutes=8), TODAY) == []
    d.price = 1363.0
    ev = await svc.run_tick(USER, T0 + timedelta(minutes=10), TODAY)
    assert [(e.strategy, e.kind, e.reason, e.pnl) for e in ev] == [
        ("base", "close", "stop", -3_420_000)
    ]
    # Vẫn tín hiệu Bullish nhưng bị chặn vào lại trong phiên
    assert await svc.run_tick(USER, T0 + timedelta(minutes=15), TODAY) == []
    v = await svc.view(USER, TODAY)
    assert v["config"]["reentry_blocked_today"] is True and v["open"] is None
    assert v["stats"]["trades"] == 1 and v["stats"]["win_rate"] == 0.0
    assert v["stats"]["by_reason"]["stop"] == 1
    eq = [t["equity"] for t in v["ticks"]]
    assert eq == [-20_000, 980_000, -3_220_000, -3_420_000, -3_420_000]
    assert v["primary"] == "base" and v["books"]["fade"]["open"]["side"] == "SHORT"
    assert v["books"]["fade"]["stats"]["trades"] == 0

    # Phiên sau → vào lại
    ev = await svc.run_tick(USER, T0 + timedelta(days=1), TODAY + timedelta(days=1))
    assert [(e.strategy, e.kind) for e in ev] == [("base", "open")]


async def test_flip_margin_alert_and_stop_sim(session) -> None:  # type: ignore[no-untyped-def]
    d = FakeDeriv()
    svc = DerivSimService(session, d)
    await svc.update_config(
        USER, active=True, clear_stop=True, clear_target=True, alert_margin_pct=5, now=T0
    )
    await svc.run_tick(USER, T0, TODAY)
    d.price = 1395.0  # +15 điểm → +1,5tr / ký quỹ ~ 25,5tr ≈ 5,9 %
    ev = await svc.run_tick(USER, T0 + timedelta(minutes=5), TODAY)
    assert [(e.strategy, e.kind) for e in ev] == [
        ("base", "margin_alert"),
        ("fade", "margin_alert"),
    ]
    assert await svc.run_tick(USER, T0 + timedelta(minutes=10), TODAY) == []  # chỉ báo 1 lần

    d.suggestion = "NGHIENG_TRANH"
    ev = await svc.run_tick(USER, T0 + timedelta(minutes=15), TODAY)
    assert [(e.kind, e.side, e.reason) for e in _p(ev)] == [
        ("close", "LONG", "signal"),
        ("open", "SHORT", "signal"),
    ]
    d.price = None  # mất giá → không ghi tick giả
    n_ticks = len((await svc.view(USER, TODAY))["ticks"])
    assert await svc.run_tick(USER, T0 + timedelta(minutes=20), TODAY) == []
    assert len((await svc.view(USER, TODAY))["ticks"]) == n_ticks

    d.price = 1390.0
    await svc.update_config(USER, active=False, now=T0 + timedelta(minutes=25))
    v = await svc.view(USER, TODAY)
    assert v["open"] is None and v["trades"][0]["exit_reason"] == "stopped"
    assert v["stats"]["trades"] == 2 and v["books"]["fade"]["stats"]["trades"] == 2

    await svc.reset(USER)
    v = await svc.view(USER, TODAY)
    assert v["trades"] == [] and v["ticks"] == [] and v["config"]["active"] is False


class CrowdDeriv(FakeDeriv):
    def __init__(self, move: float) -> None:
        super().__init__()
        self.move = move

    async def sim_context(self, today: date) -> dict:
        return {"atr": 20.0, "move": self.move}


async def test_crowd_filter_blocks_chasing_only(session) -> None:  # type: ignore[no-untyped-def]
    d = CrowdDeriv(move=18.0)  # VN30 đã +18 điểm trong phiên ≥ 0.8 × ATR 20
    svc = DerivSimService(session, d)
    await svc.update_config(USER, active=True, now=T0)
    ev = await svc.run_tick(USER, T0, TODAY)
    assert [(e.strategy, e.side) for e in ev] == [("fade", "SHORT")]  # base không đuổi theo
    d.move = 5.0
    ev = await svc.run_tick(USER, T0 + timedelta(minutes=5), TODAY)
    assert [(e.strategy, e.side) for e in ev] == [("base", "LONG")]


async def test_atr_stops_and_mode(session) -> None:  # type: ignore[no-untyped-def]
    svc = DerivSimService(session, CrowdDeriv(move=0.0))
    with pytest.raises(DerivSimError):
        await svc.update_config(USER, mode="random")
    await svc.update_config(
        USER, active=True, mode="fade", stop_atr=0.6, target_atr=1.3, avoid_round=False, now=T0
    )
    ev = await svc.run_tick(USER, T0, TODAY)
    prim = _p(ev)
    assert [(e.strategy, e.side) for e in prim] == [("fade", "SHORT")]
    assert (prim[0].stop_price, prim[0].target_price) == (1392.0, 1354.0)  # 0.6/1.3 × ATR 20
    v = await svc.view(USER, TODAY)
    assert v["primary"] == "fade" and v["open"]["side"] == "SHORT"


@dataclass
class _O:
    edge: float


@dataclass
class _RepEdge:
    suggestion: str | None
    outcomes: dict


class EdgeDeriv(FakeDeriv):
    SIGNAL_HORIZON = 20

    def __init__(self) -> None:
        super().__init__()
        self.suggestion = "NGHIENG_TRANH"
        self.edge = -0.11

    async def vn30_baserate(self) -> _RepEdge:  # type: ignore[override]
        return _RepEdge(self.suggestion, {20: _O(self.edge), 60: _O(0.5)})


async def test_service_uses_signal_horizon_edge_for_buffer(session) -> None:  # type: ignore[no-untyped-def]
    d = EdgeDeriv()
    svc = DerivSimService(session, d)
    await svc.update_config(USER, active=True, clear_stop=True, clear_target=True, now=T0)
    ev = await svc.run_tick(USER, T0, TODAY)
    assert [(e.strategy, e.side) for e in ev if e.kind == "open"] == [
        ("base", "SHORT"),
        ("fade", "LONG"),
    ]
    # Hôm sau tín hiệu về Neutral, độ lệch -8 điểm % → cả hai sổ vẫn giữ
    d.suggestion, d.edge = "TRUNG_TINH", -0.08
    assert await svc.run_tick(USER, T0 + timedelta(days=1), TODAY + timedelta(days=1)) == []
    # Độ lệch rút về -3 điểm % → đóng cả hai sổ
    d.edge = -0.03
    ev = await svc.run_tick(USER, T0 + timedelta(days=2), TODAY + timedelta(days=2))
    assert sorted((e.strategy, e.kind, e.reason) for e in ev) == [
        ("base", "close", "signal"),
        ("fade", "close", "signal"),
    ]
