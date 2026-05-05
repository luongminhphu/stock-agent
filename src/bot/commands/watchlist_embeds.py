"""Watchlist embed builders.

Owner: bot segment.
Pure presentation layer — no DB access, no service calls.
Imported by scheduler.py (WatchlistScanScheduler).
"""

from __future__ import annotations

import datetime

import discord


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
    triggered_count = getattr(result, "triggered_count", 0)

    lines: list[str] = []
    for s in signals:
        icon = "\U0001f514" if s.has_alerts else "\U0001f4ca"  # 🔔 / 📊
        lines.append(f"{icon} **{s.ticker}** {s.change_pct:+.1f}% \u2014 {s.description}")

    for r in on_signal_reminders:
        ticker = (
            r.watchlist_item.ticker
            if r.watchlist_item
            else f"item#{r.watchlist_item_id}"
        )
        lines.append(f"\u23f0 **{ticker}** \u2014 nh\u1eafc nh\u1edf theo d\u00f5i (ON_SIGNAL)")

    has_triggered = triggered_count > 0
    embed = discord.Embed(
        title="\U0001f4e1 Watchlist Scan",  # 📡
        description="\n".join(lines),
        color=0xFF6B35 if has_triggered else 0x4F98A3,
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
