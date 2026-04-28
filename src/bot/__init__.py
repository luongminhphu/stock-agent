"""Bot segment — Discord adapter + command router + scheduler.

Public API:
    create_bot()  — factory returning configured commands.Bot
    run()         — entry point (blocks)

Cogs (loaded via create_bot):
    WatchlistCog  — /watchlist add|remove|list
    ThesisCog     — /thesis add|list|close|invalidate
    MarketCog     — /quote <ticker>

Schedulers:
    BriefingScheduler       — morning brief (08:45 ICT), EOD brief (15:05 ICT)
    WatchlistScanScheduler  — auto scan every 5 min, weekdays 09:00–15:00 ICT
    Scheduler               — alias for BriefingScheduler (backward compat)

Rule: No domain logic in this segment.
      Bot = parse input → call service → format output.
"""

# Intentionally minimal — do NOT import app/run here to avoid double-import
# when running `python -m src.bot`. Consumers should import directly:
#   from src.bot.app import create_bot, run
#   from src.bot.scheduler import BriefingScheduler, WatchlistScanScheduler, Scheduler
from src.bot.scheduler import BriefingScheduler, Scheduler, WatchlistScanScheduler

__all__ = ["BriefingScheduler", "Scheduler", "WatchlistScanScheduler"]
