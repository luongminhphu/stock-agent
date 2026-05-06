"""Watchlist embed builders.

Owner: bot segment.
Pure presentation layer — no DB access, no service calls.
Imported by scheduler.py (WatchlistScanScheduler).
"""

from __future__ import annotations

import datetime

import discord

# ---------------------------------------------------------------------------
# Color standard (shared across all embed builders)
# 🟢 up/positive  🔴 down/negative  🟠 mixed/alert  🔵 info-only
# ---------------------------------------------------------------------------

_COLOR_GREEN  = 0x57F287  # Discord green
_COLOR_RED    = 0xED4245  # Discord red
_COLOR_ORANGE = 0xFF6B35  # mixed / alert
_COLOR_TEAL   = 0x4F98A3  # info-only (no directional signal)


def _price_icon(change_pct: float, has_alerts: bool) -> str:
    """Return directional color circle, or bell when alert is active."""
    if has_alerts:
        return "\U0001f514"  # 🔔
    return "\U0001f7e2" if change_pct >= 0 else "\U0001f534"  # 🟢 / 🔴


def _dominant_color(signals: list) -> int:
    """Return embed sidebar color based on majority direction of signals."""
    if not signals:
        return _COLOR_TEAL
    ups   = sum(1 for s in signals if s.change_pct >= 0)
    downs = len(signals) - ups
    if ups > downs:
        return _COLOR_GREEN
    if downs > ups:
        return _COLOR_RED
    return _COLOR_ORANGE


def build_scan_embed(
    result: object,
    now_utc: datetime.datetime,
) -> discord.Embed:
    """Build embed for WatchlistScanScheduler periodic scan notification.

    Args:
        result:   ScanResult returned by ScanService.scan_user().
        now_utc:  Current UTC datetime for footer timestamp.

    Returns:
        discord.Embed ready to send.
    """
    signals = getattr(result, "signals", []) or []
    on_signal_reminders = getattr(result, "on_signal_reminders", []) or []

    lines: list[str] = []
    for s in signals:
        icon = _price_icon(s.change_pct, s.has_alerts)
        lines.append(f"{icon} **{s.ticker}** {s.change_pct:+.1f}% \u2014 {s.description}")

    for r in on_signal_reminders:
        ticker = (
            r.watchlist_item.ticker
            if r.watchlist_item
            else f"item#{r.watchlist_item_id}"
        )
        lines.append(f"\u23f0 **{ticker}** \u2014 nh\u1eafc nh\u1edf theo d\u00f5i (ON_SIGNAL)")

    embed = discord.Embed(
        title="\U0001f4e1 Watchlist Scan",  # 📡
        description="\n".join(lines),
        color=_dominant_color(signals),
    )

    ict_time = (now_utc + datetime.timedelta(hours=7)).strftime("%H:%M ICT")
    signal_count = len(signals)
    reminder_count = len(on_signal_reminders)
    footer_parts = [f"Scan l\u00fac {ict_time}"]
    if signal_count:
        footer_parts.append(f"{signal_count} t\u00edn hi\u1ec7u")
    if reminder_count:
        footer_parts.append(f"{reminder_count} nh\u1eafc nh\u1edf")
    embed.set_footer(text=" \u2014 ".join(footer_parts))
    return embed
