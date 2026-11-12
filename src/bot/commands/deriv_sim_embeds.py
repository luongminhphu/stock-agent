"""Embed Discord cho giả lập HĐTL VN30 (DerivSimScheduler).

Một embed / lần quét, gom mọi sự kiện của sổ chính: mở lệnh, đóng lệnh (Signal /
Stop-loss / Take-profit / Roll) và cảnh báo P&L trên Margin.
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence
from typing import Any

import discord

from src.bot.discord_helper import COLORS, fmt_ict

_BOOK = {"base": "Follow", "fade": "Fade"}
_REASON = {
    "signal": "Signal",
    "stop": "Stop-loss",
    "target": "Take-profit",
    "roll": "Roll",
    "margin": "Margin",
}


def _pts(v: float | None) -> str:
    return "—" if v is None else f"{v:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _vnd(v: float | None) -> str:
    if v is None:
        return "—"
    s = f"{abs(round(v)):,}".replace(",", ".")
    return f"{'+' if v > 0 else '-' if v < 0 else ''}{s} đ"


def _line(e: Any) -> str:
    side = "Long" if e.side == "LONG" else "Short"
    if e.kind == "open":
        levels = ""
        if e.stop_price is not None or e.target_price is not None:
            levels = f" · Stop {_pts(e.stop_price)} / Target {_pts(e.target_price)}"
        return (
            f"🟢 **Mở {side}** {e.qty} × `{e.code}` @ {_pts(e.price)}"
            f" ({_REASON.get(e.reason, e.reason)}){levels}"
        )
    if e.kind == "close":
        icon = "✅" if (e.pnl or 0) > 0 else "🔻"
        return (
            f"{icon} **Đóng {side}** {e.qty} × `{e.code}` @ {_pts(e.price)}"
            f" — {_REASON.get(e.reason, e.reason)} · P&L **{_vnd(e.pnl)}**"
        )
    pct = "—" if e.pnl_on_margin_pct is None else f"{e.pnl_on_margin_pct:+.1f}%"
    return (
        f"⚠️ **P&L trên Margin {pct}** — {side} {e.qty} × `{e.code}` @ {_pts(e.price)}"
        f" · chưa chốt {_vnd(e.pnl)}"
    )


def _color(events: Sequence[Any]) -> int:
    if any(e.kind == "margin_alert" for e in events):
        return COLORS.ORANGE
    closes = [e for e in events if e.kind == "close"]
    if closes:
        return COLORS.GREEN if sum(e.pnl or 0 for e in closes) > 0 else COLORS.RED
    return COLORS.BLUE


def build_deriv_sim_embed(events: Sequence[Any], now_utc: datetime.datetime) -> discord.Embed:
    """events: SimEvent của sổ chính trong một lần quét (không rỗng)."""
    book = _BOOK.get(getattr(events[0], "strategy", "base"), "Follow")
    embed = discord.Embed(
        title=f"🧪 Giả lập VN30F · sổ {book}",
        description="\n".join(_line(e) for e in events),
        color=_color(events),
    )
    embed.set_footer(
        text=f"Paper trading theo base rate · không phải khuyến nghị · {fmt_ict(now_utc)}"
    )
    return embed
