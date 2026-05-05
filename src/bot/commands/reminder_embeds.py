"""Reminder embed builders.

Owner: bot segment.
Pure presentation layer — no DB access, no service calls.
Imported by scheduler.py (ReminderScheduler).
"""

from __future__ import annotations

import discord


def build_reminder_embed(
    ticker: str,
    freq_label: str,
    ict_time: str,
) -> discord.Embed:
    """Build embed for a single watchlist reminder notification.

    Args:
        ticker:     Stock ticker symbol, e.g. 'VCB'.
        freq_label: Human-readable frequency label, e.g. 'h\u00e0ng ng\u00e0y' or 'h\u00e0ng tu\u1ea7n'.
        ict_time:   Pre-formatted ICT time string, e.g. '08:00 ICT'.

    Returns:
        discord.Embed ready to send.
    """
    embed = discord.Embed(
        title=f"\u23f0 Nh\u1eafc nh\u1edf {freq_label}: {ticker}",
        description=(
            f"B\u1ea1n \u0111ang theo d\u00f5i **{ticker}** trong watchlist.\n"
            f"H\u00e3y ki\u1ec3m tra l\u1ea1i thesis v\u00e0 di\u1ec5n bi\u1ebfn gi\u00e1 h\u00f4m nay."
        ),
        color=0x4F98A3,
    )
    embed.set_footer(text=f"Reminder {freq_label} \u2014 {ict_time}")
    return embed
