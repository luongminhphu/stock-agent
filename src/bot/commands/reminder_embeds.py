"""Reminder embed builders.

Owner: bot segment.
Pure presentation layer — no DB access, no service calls.
Imported by scheduler.py (ReminderScheduler).
"""

from __future__ import annotations

import discord

from src.bot.discord_helper import COLORS


def build_reminder_embed(
    ticker: str,
    freq_label: str,
    ict_time: str,
) -> discord.Embed:
    """Build embed for a single watchlist reminder notification."""
    embed = discord.Embed(
        title=f"\u23f0 Nh\u1eafc nh\u1edf {freq_label}: {ticker}",
        description=(
            f"B\u1ea1n \u0111ang theo d\u00f5i **{ticker}** trong watchlist.\n"
            f"H\u00e3y ki\u1ec3m tra l\u1ea1i thesis v\u00e0 di\u1ec5n bi\u1ebfn gi\u00e1 h\u00f4m nay."
        ),
        color=COLORS.TEAL,
    )
    embed.set_footer(text=f"Reminder {freq_label} \u2014 {ict_time}")
    return embed
