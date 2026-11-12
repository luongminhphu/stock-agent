"""DerivSimScheduler — quét 5 phút trong giờ GD phái sinh, gửi embed cho sổ chính."""

from __future__ import annotations

import datetime as dt

from src.bot.commands.deriv_sim_embeds import build_deriv_sim_embed
from src.bot.scheduler import DerivSimScheduler
from src.platform.db import AsyncSessionLocal
from src.portfolio.deriv_sim_service import DerivSimService, SimEvent
from tests.portfolio.test_deriv_sim_service import FakeDeriv

UTC = dt.UTC
# Thứ Năm 12/11/2026
IN_SESSION = dt.datetime(2026, 11, 12, 3, 0, tzinfo=UTC)  # 10:00 ICT
LUNCH = dt.datetime(2026, 11, 12, 5, 0, tzinfo=UTC)  # 12:00 ICT
SATURDAY = dt.datetime(2026, 11, 14, 3, 0, tzinfo=UTC)


class _Monitor:
    def __init__(self) -> None:
        self.ok: list[str] = []
        self.fail: list[str] = []

    def register_task(self, name: str) -> None: ...

    async def record_success(self, name: str) -> None:
        self.ok.append(name)

    async def record_failure(self, name: str, exc: Exception) -> None:
        self.fail.append(f"{name}: {exc}")


class _Channel:
    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, embed=None) -> None:  # type: ignore[no-untyped-def]
        self.sent.append(embed)


class _Client:
    def __init__(self) -> None:
        self.channel = _Channel()

    def get_channel(self, _id: int) -> _Channel:
        return self.channel


async def _activate(user: str, **kw) -> None:  # type: ignore[no-untyped-def]
    async with AsyncSessionLocal() as s:
        await DerivSimService(s, FakeDeriv()).update_config(user, active=True, **kw)
        await s.commit()


async def test_runs_only_in_trading_slots_and_notifies_primary(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from src.platform.config import settings

    monkeypatch.setattr(settings, "discord_alert_channel_id", "123")
    await _activate("u1")
    client, mon = _Client(), _Monitor()
    sch = DerivSimScheduler(client, mon)  # type: ignore[arg-type]
    sch._deriv = FakeDeriv()

    assert await sch.run_once(LUNCH) == {}
    assert await sch.run_once(SATURDAY) == {}
    out = await sch.run_once(IN_SESSION)
    assert [(e.strategy, e.kind, e.side) for e in out["u1"]] == [("base", "open", "LONG")]
    assert mon.ok == ["deriv.sim"] and not mon.fail
    assert len(client.channel.sent) == 1
    assert "Mở Long" in client.channel.sent[0].description

    # Không có sự kiện → không gửi
    out = await sch.run_once(IN_SESSION + dt.timedelta(minutes=5))
    assert out == {"u1": []} and len(client.channel.sent) == 1


async def test_fade_mode_notifies_fade_book_only(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from src.platform.config import settings

    monkeypatch.setattr(settings, "discord_alert_channel_id", "123")
    await _activate("u2", mode="fade")
    client = _Client()
    sch = DerivSimScheduler(client, _Monitor())  # type: ignore[arg-type]
    sch._deriv = FakeDeriv()
    out = await sch.run_once(IN_SESSION)
    assert [(e.strategy, e.side) for e in out["u2"]] == [("fade", "SHORT")]
    assert "sổ Fade" in client.channel.sent[0].title


def test_embed_lines_and_color() -> None:
    now = dt.datetime(2026, 11, 12, 3, 0, tzinfo=UTC)
    ev = [
        SimEvent("close", "LONG", "VN30F2611", 2, 1363.0, "stop", pnl=-3_420_000),
        SimEvent("open", "SHORT", "VN30F2611", 2, 1363.0, "signal", stop_price=1378.0),
        SimEvent("margin_alert", "SHORT", "VN30F2611", 2, 1370.0, "margin", -1_420_000, -5.6),
    ]
    e = build_deriv_sim_embed(ev, now)
    assert "Stop-loss" in e.description and "-3.420.000 đ" in e.description
    assert "1.363,0" in e.description and "-5.6%" in e.description
    from src.bot.discord_helper import COLORS

    assert e.color.value == COLORS.ORANGE
