"""Embed builder for proactive watch alerts.

Owner: bot segment (formatting adapter).
No business logic — pure presentation.

Used by: bot.ProactiveWatchSubscriber
"""

from __future__ import annotations

import datetime

import discord

from src.bot.discord_helper import COLORS, fmt_ict, truncate

# ---------------------------------------------------------------------------
# Priority → sidebar colour
# ---------------------------------------------------------------------------
# 4-level urgency tiers mapped to COLORS.* semantic aliases.
# Intentionally distinct from directional market verdict colors:
#   P1 Critical → RED    (immediate action required)
#   P2 High     → ORANGE (elevated concern, watch closely)
#   P3 Medium   → BLUE   (informational, standard watch)
#   P4 Low      → GREEN  (low urgency, background monitor)
_PRIORITY_COLOURS: dict[int, int] = {
    1: COLORS.RED,     # Critical
    2: COLORS.ORANGE,  # High
    3: COLORS.BLUE,    # Medium
    4: COLORS.GREEN,   # Low
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

_PRIORITY_LABELS: dict[int, str] = {
    1: "Critical",
    2: "High",
    3: "Medium",
    4: "Low",
}


def build_proactive_watch_embed(
    ticker: str,
    condition: str,
    priority: int,
    details: str,
    triggered_at: datetime.datetime,
) -> discord.Embed:
    """Build embed for a single proactive watch alert.

    Args:
        ticker:       Stock ticker (e.g. 'VIC').
        condition:    Condition key (e.g. 'PRICE_BREAKOUT'). See _CONDITION_LABELS.
        priority:     Urgency tier 1-4 (1 = Critical, 4 = Low).
        details:      Human-readable description of the trigger.
        triggered_at: UTC datetime when the alert was triggered.

    Returns:
        discord.Embed ready to send to alert channel.
    """
    color = _PRIORITY_COLOURS.get(priority, COLORS.TEAL)
    condition_label = _CONDITION_LABELS.get(condition, condition)
    priority_label = _PRIORITY_LABELS.get(priority, str(priority))

    embed = discord.Embed(
        title=f"\U0001f6a8 Proactive Watch: {ticker.upper()}",
        description=(
            f"**Điều kiện:** {condition_label}\n"
            f"**Chi tiết:** {truncate(details, 900)}"
        ),
        color=color,
    )
    embed.add_field(name="Priority", value=f"`{priority_label}` (P{priority})", inline=True)
    embed.add_field(name="Điều kiện kỹ thuật", value=f"`{condition}`", inline=True)
    embed.set_footer(
        text=f"Triggered lúc {fmt_ict(triggered_at, fmt='%H:%M ICT')} · stock-agent"
    )
    return embed


def build_proactive_watch_batch_embed(
    alerts: list,
    now_utc: datetime.datetime,
) -> discord.Embed:
    """Build embed for a batch of proactive watch alerts.

    Args:
        alerts:  List of ProactiveWatchAlert objects (ORM or dataclass).
                 Expected attributes: ticker, condition, phase (optional),
                 priority (int 1-4).
        now_utc: UTC datetime of the batch scan.

    Returns:
        discord.Embed ready to send to alert channel.
    """
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
        p = getattr(a, "priority", "?")
        priority_label = _PRIORITY_LABELS.get(p, str(p)) if isinstance(p, int) else str(p)
        lines.append(
            f"**{getattr(a, 'ticker', '?').upper()}** \u2014 "
            f"{condition_label}{phase_str} `[{priority_label}]`"
        )

    embed = discord.Embed(
        title=f"\U0001f6a8 Proactive Watch \u2014 {len(alerts)} tín hiệu",
        description=truncate("\n".join(lines), 4096),
        color=color,
    )
    embed.set_footer(
        text=f"Scan lúc {fmt_ict(now_utc, fmt='%H:%M ICT')} · stock-agent"
    )
    return embed
