"""
Recommendation Embeds — Bot Segment, Wave 4 / Wave 7

Builds Discord Embeds from RecommendationReadyEvent.
Owner: bot segment. No domain logic here — pure formatting.

Wave 7 changes:
- build_recommendation_embed() now reads rich content directly from event fields
  (reasoning, action_detail, risk_signals, next_watch_items, thesis_id).
- kwargs are kept for backward compat — when passed they override event fields.
- thesis_id shown as inline header field when non-empty.
- Spacer field only added when thesis_id is absent (keeps 3-column row clean).
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

_URGENCY_LABEL = {
    "NOW":        "⚡ NGAY BÂY GIỜ",
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
    *,
    # Override kwargs — when passed, take priority over event fields.
    # Kept for backward compat with callers that pre-date Wave 7.
    reasoning: str | None = None,
    risk_signals: list[str] | None = None,
    next_watch_items: list[str] | None = None,
    action_detail: str | None = None,
) -> discord.Embed:
    """
    Build a rich Discord Embed for a proactive recommendation.

    Content resolution order (highest priority first):
        1. kwargs (explicit override)
        2. event fields (populated by ProactiveAlertAgent, Wave 7)
        3. empty fallback
    """
    action = event.action.upper()
    urgency = event.urgency.upper()

    action_emoji = _ACTION_EMOJI.get(action, "💡")
    urgency_label = _URGENCY_LABEL.get(urgency, urgency)
    color = _COLOR_MAP.get(action, discord.Color.blurple())

    # Resolve content — kwargs override event fields
    _reasoning        = reasoning        if reasoning        is not None else event.reasoning
    _action_detail    = action_detail    if action_detail    is not None else event.action_detail
    _risk_signals     = risk_signals     if risk_signals     is not None else list(event.risk_signals)
    _next_watch_items = next_watch_items if next_watch_items is not None else list(event.next_watch_items)

    embed = discord.Embed(
        title=f"{action_emoji} **{event.symbol}** — {action}",
        color=color,
    )

    # ── header row (inline triple) ─────────────────────────────────────────
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
    if event.thesis_id:
        # Show thesis reference in header row — fills the 3rd column slot
        embed.add_field(
            name="📋 Thesis",
            value=f"`#{event.thesis_id}`",
            inline=True,
        )
    else:
        # Zero-width spacer keeps layout consistent when no thesis
        embed.add_field(name="\u200b", value="\u200b", inline=True)

    # ── body fields ───────────────────────────────────────────────────────
    if _reasoning:
        embed.add_field(
            name="🧠 Phân tích",
            value=_reasoning[:1020],
            inline=False,
        )

    if _action_detail:
        embed.add_field(
            name="⚡ Hành động",
            value=_action_detail[:512],
            inline=False,
        )

    if _risk_signals:
        risk_text = "\n".join(f"• {r}" for r in _risk_signals[:5])
        embed.add_field(
            name="⚠️ Rủi ro",
            value=risk_text[:1020],
            inline=False,
        )

    if _next_watch_items:
        watch_text = "\n".join(f"🔍 {w}" for w in _next_watch_items[:3])
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
    Compact fallback embed — used when rich content is unavailable.
    Kept for backward compat; RecommendationListener now uses build_recommendation_embed.
    """
    action = event.action.upper()
    action_emoji = _ACTION_EMOJI.get(action, "💡")
    color = _COLOR_MAP.get(action, discord.Color.blurple())
    urgency_label = _URGENCY_LABEL.get(event.urgency.upper(), event.urgency)

    embed = discord.Embed(
        title=f"{action_emoji} {event.symbol} — {action}",
        description=f"{urgency_label} | Confidence: `{_confidence_bar(event.confidence)}`",
        color=color,
    )
    embed.set_footer(text=f"proactive_alert • {event.recommendation_id[:8]}")
    return embed
