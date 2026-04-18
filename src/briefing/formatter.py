"""Briefing formatter — converts BriefOutput to human-readable strings.

Owner: briefing segment.
Callers: bot adapters, API response builders.

This module knows Discord markdown but NOT Discord SDK.
It returns plain strings; bot/api layers decide how to send them.
"""

from __future__ import annotations

from src.ai.schemas import BriefOutput, MarketSentiment

_SENTIMENT_EMOJI = {
    MarketSentiment.RISK_ON: "🟢",
    MarketSentiment.RISK_OFF: "🔴",
    MarketSentiment.MIXED: "🟡",
    MarketSentiment.UNCERTAIN: "⚪",
}

_SENTIMENT_LABEL = {
    MarketSentiment.RISK_ON: "Risk-On",
    MarketSentiment.RISK_OFF: "Risk-Off",
    MarketSentiment.MIXED: "Mixed",
    MarketSentiment.UNCERTAIN: "Uncertain",
}


def format_brief(brief: BriefOutput, brief_type: str = "brief") -> str:
    """Format a BriefOutput as a Discord-ready markdown string.

    Args:
        brief:      Structured output from BriefingAgent.
        brief_type: Label shown in the header (e.g. "Morning Brief", "EOD Brief").

    Returns:
        Multi-line string with Discord markdown formatting.
    """
    emoji = _SENTIMENT_EMOJI.get(brief.sentiment, "⚪")
    label = _SENTIMENT_LABEL.get(brief.sentiment, str(brief.sentiment))

    lines: list[str] = [
        f"**📈 {brief_type.title()}** {emoji} `{label}`",
        "",
        f"**{brief.headline}**",
        "",
        brief.summary,
    ]

    if brief.key_movers:
        lines += ["", "**🔥 Key Movers**"]
        for mover in brief.key_movers:
            lines.append(f"\u2022 {mover}")

    if brief.watchlist_alerts:
        lines += ["", "**👁️ Watchlist**"]
        for alert in brief.watchlist_alerts:
            lines.append(f"\u2022 {alert}")

    if brief.action_items:
        lines += ["", "**✅ Action Items**"]
        for item in brief.action_items:
            lines.append(f"\u2022 {item}")

    return "\n".join(lines)


def format_morning_brief(brief: BriefOutput) -> str:
    """Convenience wrapper for morning brief formatting."""
    return format_brief(brief, brief_type="Morning Brief")


def format_eod_brief(brief: BriefOutput) -> str:
    """Convenience wrapper for EOD brief formatting."""
    return format_brief(brief, brief_type="EOD Brief")
