"""Thesis aggregate embed builder.

Owner: bot segment.
Pure presentation layer — no DB access, no service calls.
Imported by thesis_crud.py (ThesisCrudCog./thesis aggregate).

Input:  dict from DashboardService.get_thesis_portfolio_aggregate()
Output: discord.Embed ready to send
"""

from __future__ import annotations

import datetime

import discord

from src.bot.discord_helper import COLORS, fmt_ict


def _pnl_color(pnl_pct: float | None) -> int:
    """Return sidebar colour based on aggregate P&L direction."""
    if pnl_pct is None:
        return COLORS.TEAL
    if pnl_pct > 3:
        return COLORS.GREEN
    if pnl_pct < -3:
        return COLORS.RED
    return COLORS.GOLD


def _fmt_vnd(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.0f} VND"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def _breakdown_bar(counts: dict[str, int], keys: list[str], icons: dict[str, str]) -> str:
    """Render a one-line breakdown string: 🟢 buy×3  🟡 hold×2  …"""
    parts = []
    for k in keys:
        n = counts.get(k, 0)
        if n > 0:
            icon = icons.get(k, "")
            parts.append(f"{icon} **{k}** \u00d7{n}")
    return "  ".join(parts) if parts else "—"


def build_aggregate_embed(data: dict) -> discord.Embed:
    """Build a Discord Embed from a thesis portfolio aggregate dict."""
    total     = data.get("total_theses", 0)
    pnl_pct   = data.get("total_pnl_pct")
    pnl_abs   = data.get("total_pnl_abs")
    cost      = data.get("total_cost_basis")
    market    = data.get("total_market_value")
    with_pos  = data.get("with_position_count", 0)
    reviewed  = data.get("reviewed_count", 0)

    verdict_bd: dict[str, int] = data.get("verdict_breakdown", {})
    tier_bd:    dict[str, int] = data.get("tier_breakdown",    {})
    pnl_bd:     dict[str, int] = data.get("pnl_breakdown",     {})

    generated_at: str | None = data.get("generated_at")

    embed = discord.Embed(
        title=f"\U0001f4ca Thesis Portfolio Summary \u2014 {total} theses active",
        color=_pnl_color(pnl_pct),
    )

    if cost is not None or market is not None or pnl_abs is not None:
        pnl_line = _fmt_pct(pnl_pct)
        if pnl_abs is not None:
            pnl_line += f"  ({_fmt_vnd(pnl_abs)})"
        embed.add_field(
            name="\U0001f4b0 P&L tổng danh mục",
            value=(
                f"Vốn: `{_fmt_vnd(cost)}`\n"
                f"Market value: `{_fmt_vnd(market)}`\n"
                f"P&L: `{pnl_line}`"
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="\U0001f4b0 P&L tổng danh mục",
            value="_Chưa có dữ liệu position / giá._",
            inline=False,
        )

    embed.add_field(
        name="\U0001f4c1 Coverage",
        value=(
            f"Có open position: **{with_pos}** / {total}\n"
            f"Đã review AI: **{reviewed}** / {total}"
        ),
        inline=False,
    )

    verdict_icons = {
        "bullish":   "\U0001f7e2",
        "bearish":   "\U0001f534",
        "neutral":   "\U0001f7e1",
        "watchlist": "\U0001f535",
        "none":      "\u26aa",
    }
    embed.add_field(
        name="\U0001f9e0 Verdict breakdown",
        value=_breakdown_bar(verdict_bd, ["bullish", "neutral", "bearish", "watchlist", "none"], verdict_icons),
        inline=False,
    )

    tier_icons = {
        "Strong":   "\U0001f4ce",
        "Healthy":  "\U0001f7e2",
        "Moderate": "\U0001f7e1",
        "Weak":     "\U0001f7e0",
        "Critical": "\U0001f534",
        "none":     "\u26aa",
    }
    embed.add_field(
        name="\U0001f3c6 Score tier breakdown",
        value=_breakdown_bar(tier_bd, ["Strong", "Healthy", "Moderate", "Weak", "Critical", "none"], tier_icons),
        inline=False,
    )

    pnl_icons = {
        "profit":  "\U0001f7e2",
        "neutral": "\U0001f7e1",
        "loss":    "\U0001f534",
        "none":    "\u26aa",
    }
    embed.add_field(
        name="\U0001f4c8 P&L status breakdown",
        value=_breakdown_bar(pnl_bd, ["profit", "neutral", "loss", "none"], pnl_icons),
        inline=False,
    )

    if generated_at:
        try:
            dt_utc = datetime.datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            ts_str = fmt_ict(dt_utc, fmt="%H:%M ICT %d/%m/%Y")
        except (ValueError, AttributeError):
            ts_str = generated_at
        embed.set_footer(text=f"Cập nhật lúc {ts_str} \u00b7 stock-agent")

    return embed
