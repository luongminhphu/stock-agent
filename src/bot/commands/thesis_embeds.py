"""Thesis embed builders and display constants.

Owner: bot segment.
Pure presentation layer — no DB access, no service calls.
Imported by thesis_crud.py and thesis_review.py.
"""

from __future__ import annotations

import json

import discord

from src.thesis.models import ReviewVerdict, ThesisStatus

# ---------------------------------------------------------------------------
# Display constants
# ---------------------------------------------------------------------------

_VERDICT_COLOUR: dict[ReviewVerdict, discord.Color] = {
    ReviewVerdict.BULLISH: discord.Color.green(),
    ReviewVerdict.BEARISH: discord.Color.red(),
    ReviewVerdict.NEUTRAL: discord.Color.yellow(),
    ReviewVerdict.WATCHLIST: discord.Color.blue(),
}

_VERDICT_ICON: dict[ReviewVerdict, str] = {
    ReviewVerdict.BULLISH: "🟢",
    ReviewVerdict.BEARISH: "🔴",
    ReviewVerdict.NEUTRAL: "🟡",
    ReviewVerdict.WATCHLIST: "🔵",
}

STATUS_ICON: dict[ThesisStatus, str] = {
    ThesisStatus.ACTIVE: "🟢",
    ThesisStatus.PAUSED: "⏸️",
    ThesisStatus.INVALIDATED: "❌",
    ThesisStatus.CLOSED: "✅",
}

TARGET_ICON: dict[str, str] = {
    "assumption": "📌",
    "catalyst": "⚡",
}


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------


def build_review_embed(review: object) -> discord.Embed:
    """Build a rich embed from a ThesisReview ORM object."""
    verdict = ReviewVerdict(review.verdict)  # type: ignore[attr-defined]
    colour = _VERDICT_COLOUR.get(verdict, discord.Color.greyple())
    icon = _VERDICT_ICON.get(verdict, "⚪")

    embed = discord.Embed(
        title=f"{icon} Thesis #{review.thesis_id} — {verdict.value}",  # type: ignore[attr-defined]
        description=review.reasoning[:1000] if review.reasoning else "",  # type: ignore[attr-defined]
        colour=colour,
    )
    embed.add_field(
        name="Confidence",
        value=f"{confidence_bar(review.confidence)} `{review.confidence:.0%}`",  # type: ignore[attr-defined]
        inline=False,
    )

    try:
        risks = json.loads(review.risk_signals or "[]")  # type: ignore[attr-defined]
    except (json.JSONDecodeError, TypeError):
        risks = []
    if risks:
        embed.add_field(
            name="⚠️ Risk Signals",
            value="\n".join(f"• {r}" for r in risks[:5]),
            inline=False,
        )

    try:
        watches = json.loads(review.next_watch_items or "[]")  # type: ignore[attr-defined]
    except (json.JSONDecodeError, TypeError):
        watches = []
    if watches:
        embed.add_field(
            name="👁️ Watch Next",
            value="\n".join(f"• {w}" for w in watches[:5]),
            inline=False,
        )

    price_str = (
        f"{review.reviewed_price:,.0f} VND"  # type: ignore[attr-defined]
        if review.reviewed_price  # type: ignore[attr-defined]
        else "N/A"
    )
    reviewed_at = getattr(review, "reviewed_at", None)
    ts_str = reviewed_at.strftime("%H:%M %d/%m/%Y") if reviewed_at else "N/A"
    embed.set_footer(text=f"Price at review: {price_str} • {ts_str} • stock-agent AI")
    return embed


def confidence_bar(confidence: float, length: int = 10) -> str:
    """Return a Unicode progress bar for a 0..1 confidence value."""
    filled = round(confidence * length)
    return "█" * filled + "░" * (length - filled)
