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

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_COLOR_GREEN = 0x57F287   # profit
_COLOR_RED   = 0xED4245   # loss
_COLOR_TEAL  = 0x4F98A3   # neutral / no data
_COLOR_GOLD  = 0xFCC419   # mixed / borderline


def _pnl_color(pnl_pct: float | None) -> int:
    """Return sidebar colour based on aggregate P&L direction."""
    if pnl_pct is None:
        return _COLOR_TEAL
    if pnl_pct > 3:
        return _COLOR_GREEN
    if pnl_pct < -3:
        return _COLOR_RED
    return _COLOR_GOLD


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
            parts.append(f"{icon} **{k}** ×{n}")
    return "  ".join(parts) if parts else "—"


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_aggregate_embed(data: dict) -> discord.Embed:
    """Build a Discord Embed from a thesis portfolio aggregate dict.

    Args:
        data: dict returned by DashboardService.get_thesis_portfolio_aggregate()

    Returns:
        discord.Embed ready to pass to interaction.followup.send(embed=...)
    """
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

    color = _pnl_color(pnl_pct)

    embed = discord.Embed(
        title=f"📊 Thesis Portfolio Summary — {total} theses active",
        color=color,
    )

    # ── P&L block ─────────────────────────────────────────────────────
    if cost is not None or market is not None or pnl_abs is not None:
        pnl_line = _fmt_pct(pnl_pct)
        if pnl_abs is not None:
            pnl_line += f"  ({_fmt_vnd(pnl_abs)})"

        embed.add_field(
            name="💰 P&L tổng danh mục",
            value=(
                f"Vốn: `{_fmt_vnd(cost)}`\n"
                f"Market value: `{_fmt_vnd(market)}`\n"
                f"P&L: `{pnl_line}`"
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="💰 P&L tổng danh mục",
            value="_Chưa có dữ liệu position / giá._",
            inline=False,
        )

    # ── Coverage block ─────────────────────────────────────────────────
    embed.add_field(
        name="📁 Coverage",
        value=(
            f"Có open position: **{with_pos}** / {total}\n"
            f"Đã review AI: **{reviewed}** / {total}"
        ),
        inline=False,
    )

    # ── Verdict breakdown ──────────────────────────────────────────────
    verdict_icons = {
        "bullish":   "🟢",
        "bearish":   "🔴",
        "neutral":   "🟡",
        "watchlist": "🔵",
        "none":      "⚪",
    }
    embed.add_field(
        name="🧠 Verdict breakdown",
        value=_breakdown_bar(
            verdict_bd,
            ["bullish", "neutral", "bearish", "watchlist", "none"],
            verdict_icons,
        ),
        inline=False,
    )

    # ── Tier breakdown ─────────────────────────────────────────────────
    tier_icons = {
        "Strong":   "📎",
        "Healthy":  "🟢",
        "Moderate": "🟡",
        "Weak":     "🟠",
        "Critical": "🔴",
        "none":     "⚪",
    }
    embed.add_field(
        name="🏆 Score tier breakdown",
        value=_breakdown_bar(
            tier_bd,
            ["Strong", "Healthy", "Moderate", "Weak", "Critical", "none"],
            tier_icons,
        ),
        inline=False,
    )

    # ── P&L status breakdown ───────────────────────────────────────────
    pnl_icons = {
        "profit":  "🟢",
        "neutral": "🟡",
        "loss":    "🔴",
        "none":    "⚪",
    }
    embed.add_field(
        name="📈 P&L status breakdown",
        value=_breakdown_bar(
            pnl_bd,
            ["profit", "neutral", "loss", "none"],
            pnl_icons,
        ),
        inline=False,
    )

    # ── Footer ─────────────────────────────────────────────────────────
    if generated_at:
        try:
            dt_utc = datetime.datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            dt_ict = dt_utc + datetime.timedelta(hours=7)
            ts_str = dt_ict.strftime("%H:%M ICT %d/%m/%Y")
        except (ValueError, AttributeError):
            ts_str = generated_at
        embed.set_footer(text=f"Cập nhật lúc {ts_str} · stock-agent")

    return embed
