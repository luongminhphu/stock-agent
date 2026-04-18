"""Bot segment — Discord adapter + command router + scheduler.

Public API:
    create_bot()  — factory returning configured commands.Bot
    run()         — entry point (blocks)

Cogs (loaded via create_bot):
    WatchlistCog  — /watchlist add|remove|list
    ThesisCog     — /thesis add|list|close|invalidate
    MarketCog     — /quote <ticker>

Scheduler:
    Scheduler     — morning_brief (08:30), watchlist_scan (15 min)

Rule: No domain logic in this segment.
      Bot = parse input → call service → format output.
"""
from src.bot.app import create_bot, run
from src.bot.scheduler import Scheduler

__all__ = ["create_bot", "run", "Scheduler"]
