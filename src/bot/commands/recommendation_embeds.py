"""
Recommendation Embeds — Bot Segment, Wave 4

Builds Discord Embeds from RecommendationReadyEvent / ProactiveRecommendation.
Owner: bot segment. No domain logic here — pure formatting.
"""
from __future__ import annotations

import discord

from src.platform.events import RecommendationReadyEvent

# ── constants ──────────────────────────────────────────────────────────────

_ACTION_EMOJI = {
    "BUY":    "🟢",
    "SELL":   "🔴",
    "REDUCE": "🟡",
    "HOLD":   "⏸️",
    "WATCH":  "👁️",
}

_URGENCY_EMOJI = {
    "NOW":        "⚡ NGAY BÂY GIờ",
    "TODAY":      "🕒 HÔM NAY",
    "THIS_WEEK":  "📅 TUẦN NÀY",
    "MONITORING": "🔭 THEO DÕI",
}

_COLOR_MAP = {
    "BUY":    discord.Color.green(),
    "SELL":   discord.Color.red(),
    "REDUCE": discord.Color.orange(),
    "HOLD":   discord.Color.light_grey(),
    "WATCH":  discord.Color.blue(),
}

_CONFIDENCE_BAR_LEN = 10


def _confidence_bar(value: float) -> str:
    filled = round(value * _CONFIDENCE_BAR_LEN)
    return "█" * filled + "░" * (_CONFIDENCE_BAR_LEN - filled) + f" {value * 100:.0f}%"


# ── embed builders ──────────────────────────────────────────────────────────


def build_recommendation_embed(
    event: RecommendationReadyEvent,
    reasoning: str = "",
    risk_signals: list[str] | None = None,
    next_watch_items: list[str] | None = None,
    action_detail: str = "",
) -> discord.Embed:
    """
    Build a rich Discord Embed for a proactive recommendation.

    Args:
        event:           The RecommendationReadyEvent from the bus.
        reasoning:       Short AI reasoning string (1-3 sentences).
        risk_signals:    List of risk bullet points.
        next_watch_items: List of follow-up items.
        action_detail:   Specific action text (e.g. 'Mua breakout trên 95,000').
    """
    action = event.action.upper()
    urgency = event.urgency.upper()

    action_emoji = _ACTION_EMOJI.get(action, "💡")
    urgency_label = _URGENCY_EMOJI.get(urgency, urgency)
    color = _COLOR_MAP.get(action, discord.Color.blurple())

    title = f"{action_emoji} **{event.symbol}** — {action}"

    embed = discord.Embed(
        title=title,
        color=color,
    )

    embed.add_field(
        name="⏰ Độ khẩn",
        value=urgency_label,
        inline=True,
    )
    embed.add_field(
        name="📊 Độ tin cậy",
        value=f"`{_confidence_bar(event.confidence)}`",
        inline=True,
    )
    embed.add_field(name="​", value="​", inline=True)  # spacer

    if reasoning:
        embed.add_field(
            name="🧠 Phân tích",
            value=reasoning[:1020],
            inline=False,
        )

    if action_detail:
        embed.add_field(
            name="⚡ Hành động",
            value=action_detail[:512],
            inline=False,
        )

    if risk_signals:
        risk_text = "\n".join(f"• {r}" for r in risk_signals[:5])
        embed.add_field(
            name="⚠️ Rủi ro",
            value=risk_text[:1020],
            inline=False,
        )

    if next_watch_items:
        watch_text = "\n".join(f"🔍 {w}" for w in next_watch_items[:3])
        embed.add_field(
            name="📌 Theo dõi tiếp",
            value=watch_text[:512],
            inline=False,
        )

    embed.set_footer(
        text=f"source: {event.source_agent} • id: {event.recommendation_id[:8]}"
    )

    return embed


def build_simple_alert_embed(event: RecommendationReadyEvent) -> discord.Embed:
    """
    Compact embed — used when AI detail is not available (fallback or MONITORING urgency).
    """
    action = event.action.upper()
    action_emoji = _ACTION_EMOJI.get(action, "💡")
    color = _COLOR_MAP.get(action, discord.Color.blurple())
    urgency_label = _URGENCY_EMOJI.get(event.urgency.upper(), event.urgency)

    embed = discord.Embed(
        title=f"{action_emoji} {event.symbol} — {action}",
        description=f"{urgency_label} | Confidence: `{_confidence_bar(event.confidence)}`",
        color=color,
    )
    embed.set_footer(text=f"proactive_alert • {event.recommendation_id[:8]}")
    return embed
