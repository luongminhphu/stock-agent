"""Scheduler — cron-based task orchestration.

Owner: bot segment (thin adapter only).
The scheduler triggers domain services; it does NOT implement business logic.

Wave 1: task stubs only.
Wave 2: wire BriefingService, ScanService.

Usage: called from bot on_ready after cogs are loaded.
"""
from __future__ import annotations

from discord.ext import tasks
from discord.ext import commands

from src.platform.logging import get_logger

logger = get_logger(__name__)


class Scheduler:
    """Manages periodic background tasks for the bot.

    All task methods must stay thin:
        1. Determine which users/tickers to process
        2. Call the appropriate service
        3. Deliver result via bot channel/DM

    No business logic in task methods.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def start_all(self) -> None:
        """Start all scheduled tasks. Call once after bot is ready."""
        self.morning_brief.start()
        self.watchlist_scan.start()
        logger.info("scheduler.started")

    def stop_all(self) -> None:
        self.morning_brief.cancel()
        self.watchlist_scan.cancel()
        logger.info("scheduler.stopped")

    @tasks.loop(time=__import__("datetime").time(hour=8, minute=30))  # 08:30 VN time
    async def morning_brief(self) -> None:
        """Trigger morning brief generation for all active users.

        Wave 2: call BriefingService.generate_morning_brief() per user.
        """
        logger.info("scheduler.morning_brief.tick")
        # TODO Wave 2: iterate users, call briefing service, deliver to Discord channel

    @tasks.loop(minutes=15)
    async def watchlist_scan(self) -> None:
        """Scan watchlist for triggered alerts every 15 minutes during market hours.

        Wave 2: call ScanService.scan_user(), deliver alerts via DM or channel.
        """
        logger.info("scheduler.watchlist_scan.tick")
        # TODO Wave 2: market hours guard, iterate users, scan, notify
