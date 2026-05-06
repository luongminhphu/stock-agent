"""Thesis embed builders and display constants.

Owner: bot segment.
Pure presentation layer — no DB access, no service calls.
Imported by thesis_crud.py, thesis_review.py, and scheduler.py.
"""

from __future__ import annotations

import datetime
import json

import discord

from src.thesis.models import ReviewVerdict, ThesisStatus

# ---------------------------------------------------------------------------
# Display constants
# ---------------------------------------------------------------------------

_VERDICT_COLOUR: dict[ReviewVerdict, discord.Color] = {
    ReviewVerdict.BULLISH:   discord.Color.green(),
    ReviewVerdict.BEARISH:   discord.Color.red(),
    ReviewVerdict.NEUTRAL:   discord.Color.yellow(),
    ReviewVerdict.WATCHLIST: discord.Color.blue(),
}

_VERDICT_ICON: dict[ReviewVerdict, str] = {
    ReviewVerdict.BULLISH:   "\U0001f7e2",   # 🟢
    ReviewVerdict.BEARISH:   "\U0001f534",   # 🔴
    ReviewVerdict.NEUTRAL:   "\U0001f7e1",   # 🟡
    ReviewVerdict.WATCHLIST: "\U0001f535",   # 🔵
}

STATUS_ICON: dict[ThesisStatus, str] = {
    ThesisStatus.ACTIVE:      "\U0001f7e2",     # 🟢
    ThesisStatus.PAUSED:      "\u23f8\ufe0f",  # ⏸️
    ThesisStatus.INVALIDATED: "\u274c",         # ❌
    ThesisStatus.CLOSED:      "\u2705",          # ✅
}

TARGET_ICON: dict[str, str] = {
    "assumption": "\U0001f4cc",  # 📌
    "catalyst":   "\u26a1",      # ⚡
}

# Drift verdict → icon (string keys from AI output)
_DRIFT_VERDICT_ICON: dict[str, str] = {
    "bullish": "\U0001f7e2",   # 🟢
    "bearish": "\U0001f534",   # 🔴
    "neutral": "\U0001f7e1",   # 🟡
}

# ---------------------------------------------------------------------------
# Color standard (shared across all embed builders)
# ---------------------------------------------------------------------------

_COLOR_GREEN  = 0x57F287
_COLOR_RED    = 0xED4245
_COLOR_ORANGE = 0xFF6B35
_COLOR_TEAL   = 0x4F98A3


def _dominant_verdict_color(reviews: list) -> int:
    """Derive sidebar color from dominant verdict in a list of ThesisReview objects."""
    bullish = sum(1 for r in reviews if str(r.verdict).upper() == "BULLISH")
    bearish = sum(1 for r in reviews if str(r.verdict).upper() == "BEARISH")
    if bullish > bearish:
        return _COLOR_GREEN
    if bearish > bullish:
        return _COLOR_RED
    return _COLOR_TEAL  # neutral/mixed → default info color


def _dominant_drift_color(reviews: list[tuple]) -> int:
    """Derive sidebar color from dominant drift verdict in (DriftSignal, ThesisReview) tuples."""
    bullish = sum(1 for _, r in reviews if str(r.verdict).upper() == "BULLISH")
    bearish = sum(1 for _, r in reviews if str(r.verdict).upper() == "BEARISH")
    if bullish > bearish:
        return _COLOR_GREEN
    if bearish > bullish:
        return _COLOR_RED
    return _COLOR_ORANGE  # drift alert with no clear direction → orange


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------


def build_review_embed(review: object) -> discord.Embed:
    """Build a rich embed from a ThesisReview ORM object."""
    verdict = ReviewVerdict(review.verdict)  # type: ignore[attr-defined]
    colour = _VERDICT_COLOUR.get(verdict, discord.Color.greyple())
    icon = _VERDICT_ICON.get(verdict, "\u26aa")

    embed = discord.Embed(
        title=f"{icon} Thesis #{review.thesis_id} \u2014 {verdict.value}",  # type: ignore[attr-defined]
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
            name="\u26a0\ufe0f Risk Signals",
            value="\n".join(f"\u2022 {r}" for r in risks[:5]),
            inline=False,
        )

    try:
        watches = json.loads(review.next_watch_items or "[]")  # type: ignore[attr-defined]
    except (json.JSONDecodeError, TypeError):
        watches = []
    if watches:
        embed.add_field(
            name="\U0001f441\ufe0f Watch Next",
            value="\n".join(f"\u2022 {w}" for w in watches[:5]),
            inline=False,
        )

    price_str = (
        f"{review.reviewed_price:,.0f} VND"  # type: ignore[attr-defined]
        if review.reviewed_price  # type: ignore[attr-defined]
        else "N/A"
    )
    reviewed_at = getattr(review, "reviewed_at", None)
    ts_str = reviewed_at.strftime("%H:%M %d/%m/%Y") if reviewed_at else "N/A"
    embed.set_footer(text=f"Price at review: {price_str} \u2022 {ts_str} \u2022 stock-agent AI")
    return embed


def build_maintenance_embed(
    expired_count: int,
    reviews: list,
    now_utc: datetime.datetime,
) -> discord.Embed:
    """Build embed for ThesisMaintenanceScheduler daily summary.

    Args:
        expired_count: Number of catalysts auto-expired.
        reviews:       List of ThesisReview ORM objects from review_stale_theses().
        now_utc:       Current UTC datetime for footer timestamp.

    Returns:
        discord.Embed ready to send.
    """
    lines: list[str] = []
    if expired_count:
        lines.append(f"\u23f0 **{expired_count}** catalyst \u0111\u00e3 h\u1ebft h\u1ea1n \u2192 EXPIRED")
    for r in reviews:
        try:
            verdict_enum = ReviewVerdict(r.verdict)
            icon = _VERDICT_ICON.get(verdict_enum, "\u26aa")
        except (ValueError, KeyError):
            icon = "\u26aa"
        lines.append(
            f"{icon} Thesis #{r.thesis_id} \u2014 {r.verdict} "
            f"(confidence: {r.confidence:.0%})"
        )

    embed = discord.Embed(
        title="\U0001f527 Thesis Maintenance",  # 🔧
        description="\n".join(lines),
        color=_dominant_verdict_color(reviews) if reviews else _COLOR_TEAL,
    )
    ict_time = (now_utc + datetime.timedelta(hours=7)).strftime("%H:%M ICT")
    embed.set_footer(text=f"Auto-maintenance l\u00fac {ict_time}")
    return embed


def build_drift_embed(
    reviews: list[tuple],
    now_utc: datetime.datetime,
) -> discord.Embed:
    """Build embed for ThesisDriftScheduler drift alert notification.

    Args:
        reviews:  List of (DriftSignal, ThesisReview) tuples from drift task.
        now_utc:  Current UTC datetime for footer timestamp.

    Returns:
        discord.Embed ready to send.
    """
    lines: list[str] = []
    for signal, review in reviews:
        icon = _DRIFT_VERDICT_ICON.get(str(review.verdict).lower(), "\u26aa")
        lines.append(
            f"{icon} **{signal.ticker}** {signal.direction}{abs(signal.drift_pct):.1f}% "
            f"drift \u2192 AI verdict: **{review.verdict}** "
            f"(confidence {review.confidence:.0%})"
        )

    from src.platform.config import settings  # lazy import — avoids circular at module level
    ict_time = (now_utc + datetime.timedelta(hours=7)).strftime("%H:%M ICT")
    embed = discord.Embed(
        title="\u26a1 Thesis Drift Alert",
        description="\n".join(lines),
        color=_dominant_drift_color(reviews),
    )
    embed.set_footer(
        text=f"Drift \u2265{settings.thesis_drift_threshold_pct:.0f}% detected l\u00fac {ict_time}"
    )
    return embed


def confidence_bar(confidence: float, length: int = 10) -> str:
    """Return a Unicode progress bar for a 0..1 confidence value."""
    filled = round(confidence * length)
    return "\u2588" * filled + "\u2591" * (length - filled)
