"""Embed builder for proactive watch alerts.

Owner: bot segment (formatting adapter).
No business logic — pure presentation.

Used by: bot.ProactiveWatchSubscriber
"""

from __future__ import annotations

import datetime

import discord

from src.bot.discord_helper import COLORS, fmt_ict

# Priority → sidebar colour
# NOTE: these are intentionally distinct from COLORS.* palette —
# they represent 4-level urgency tiers, not directional market signals.
_PRIORITY_COLOURS: dict[int, int] = {
    1: 0xE74C3C,  # Critical — Flat Red
    2: 0xF39C12,  # High     — Amber
    3: 0x3498DB,  # Medium   — Blue
    4: 0x2ECC71,  # Low      — Muted Green
}

_CONDITION_LABELS: dict[str, str] = {
    "PRICE_BREAKOUT":   "Breakout giá",
    "VOLUME_SURGE":     "Volume đột biến",
    "MA_CROSS":         "MA cross",
    "RSI_EXTREME":      "RSI cực trị",
    "SUPPORT_BREAK":    "Hỗ trợ gãy",
    "RESISTANCE_TOUCH": "Chạm kháng cự",
}

_PHASE_LABELS: dict[str, str] = {
    "ACCUMULATION": "Tích lũy",
    "MARKUP":       "Tăng mạnh",
    "DISTRIBUTION": "Phân phối",
    "DECLINE":      "Giảm",
}


def build_proactive_watch_embed(
    ticker: str,
    condition: str,
    priority: int,
    details: str,
    triggered_at: datetime.datetime,
) -> discord.Embed:
    """Build embed for a single proactive watch alert."""
    color = _PRIORITY_COLOURS.get(priority, COLORS.TEAL)
    condition_label = _CONDITION_LABELS.get(condition, condition)

    embed = discord.Embed(
        title=f"\U0001f6a8 Proactive Watch: {ticker}",
        description=(
            f"**Điều kiện:** {condition_label}\n"
            f"**Chi tiết:** {details}"
        ),
        color=color,
    )
    embed.add_field(name="Priority", value=f"`{priority}`", inline=True)
    embed.add_field(name="Điều kiện kỹ thuật", value=f"`{condition}`", inline=True)
    embed.set_footer(text=f"Triggered lúc {fmt_ict(triggered_at, fmt='%H:%M ICT')}")
    return embed


def build_proactive_watch_batch_embed(
    alerts: list,
    now_utc: datetime.datetime,
) -> discord.Embed:
    """Build embed for a batch of proactive watch alerts."""
    if not alerts:
        return discord.Embed(
            title="\U0001f4e1 Proactive Watch",
            description="Không có tín hiệu mới.",
            color=COLORS.TEAL,
        )

    min_priority = min(getattr(a, "priority", 4) for a in alerts)
    color = _PRIORITY_COLOURS.get(min_priority, COLORS.TEAL)

    lines: list[str] = []
    for a in alerts:
        condition_label = _CONDITION_LABELS.get(
            getattr(a, "condition", ""), getattr(a, "condition", "?")
        )
        phase_label = _PHASE_LABELS.get(
            getattr(a, "phase", ""), getattr(a, "phase", "")
        )
        phase_str = f" ({phase_label})" if phase_label else ""
        lines.append(
            f"**{a.ticker}** \u2014 {condition_label}{phase_str} `[P{getattr(a, 'priority', '?')}]`"
        )

    embed = discord.Embed(
        title=f"\U0001f6a8 Proactive Watch \u2014 {len(alerts)} tín hiệu",
        description="\n".join(lines),
        color=color,
    )
    embed.set_footer(text=f"Scan lúc {fmt_ict(now_utc, fmt='%H:%M ICT')}")
    return embed
