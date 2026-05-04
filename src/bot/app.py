"""Discord bot application factory.

Owner: bot segment.
This module only wires the runtime — no business logic here.
All domain operations are delegated to segment services.
"""

from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from src.platform.bootstrap import bootstrap
from src.platform.config import settings
from src.platform.logging import get_logger

logger = get_logger(__name__)


def create_bot() -> commands.Bot:
    """Create and configure the Discord bot instance."""
    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(
        command_prefix="/",
        intents=intents,
        help_command=None,
    )

    @bot.event
    async def on_ready() -> None:
        await bootstrap()
        await _register_cogs(bot)
        _start_briefing_scheduler(bot)
        _start_scan_scheduler(bot)
        _start_thesis_maintenance_scheduler(bot)
        _start_drift_scheduler(bot)
        _start_snapshot_scheduler()
        _start_reminder_scheduler(bot)
        _start_decision_replay_scheduler(bot)
        await _sync_tree(bot)
        logger.info(
            "bot.ready",
            user=str(bot.user),
            guild_count=len(bot.guilds),
        )

    @bot.event
    async def on_error(event: str, *args: object, **kwargs: object) -> None:
        logger.exception("bot.event_error", event_name=event)

    return bot


def run() -> None:
    """Entry point called by src/bot/__main__.py."""
    bot = create_bot()
    asyncio.run(bot.start(settings.discord_token))


async def _sync_tree(bot: commands.Bot) -> None:
    if settings.discord_guild_id:
        guild = discord.Object(id=int(settings.discord_guild_id))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        logger.info("bot.tree.synced", mode="guild", guild_id=settings.discord_guild_id)
    else:
        await bot.tree.sync()
        logger.info("bot.tree.synced", mode="global")


async def _register_cogs(bot: commands.Bot) -> None:
    """Load all command cogs. Add new cogs here only — no logic."""
    from src.bot.commands.briefing import BriefingCog
    from src.bot.commands.conviction_timeline import ConvictionTimelineCog
    from src.bot.commands.decision import DecisionCog
    from src.bot.commands.health import HealthCog
    from src.bot.commands.help import HelpCog
    from src.bot.commands.market import MarketCog
    from src.bot.commands.portfolio import PortfolioCog
    from src.bot.commands.pretrade import PretradeCog
    from src.bot.commands.scheduler_trigger import SchedulerTriggerCog
    from src.bot.commands.stress_test import StressTestCog
    from src.bot.commands.thesis_crud import ThesisCrudCog
    from src.bot.commands.thesis_review import ThesisReviewCog
    from src.bot.commands.watchlist import WatchlistCog
    from src.bot.commands.why import WhyCog

    await bot.add_cog(WatchlistCog(bot))
    await bot.add_cog(ThesisCrudCog(bot))
    await bot.add_cog(ThesisReviewCog(bot))
    await bot.add_cog(MarketCog(bot))
    await bot.add_cog(BriefingCog(bot))
    await bot.add_cog(HelpCog(bot))
    await bot.add_cog(WhyCog(bot))
    await bot.add_cog(PretradeCog(bot))
    await bot.add_cog(ConvictionTimelineCog(bot))
    await bot.add_cog(PortfolioCog(bot))
    await bot.add_cog(StressTestCog(bot))
    await bot.add_cog(DecisionCog(bot))
    await bot.add_cog(SchedulerTriggerCog(bot))
    await bot.add_cog(HealthCog(bot))
    logger.info(
        "bot.cogs_loaded",
        cogs=[
            "WatchlistCog", "ThesisCrudCog", "ThesisReviewCog", "MarketCog",
            "BriefingCog", "HelpCog", "WhyCog", "PretradeCog",
            "ConvictionTimelineCog", "PortfolioCog", "StressTestCog",
            "DecisionCog", "SchedulerTriggerCog", "HealthCog",
        ],
    )


def _start_briefing_scheduler(bot: commands.Bot) -> None:
    from src.bot.scheduler import BriefingScheduler
    scheduler = BriefingScheduler(bot)
    scheduler.start()


def _start_scan_scheduler(bot: commands.Bot) -> None:
    from src.bot.scheduler import WatchlistScanScheduler
    scheduler = WatchlistScanScheduler(bot)
    scheduler.start()


def _start_thesis_maintenance_scheduler(bot: commands.Bot) -> None:
    from src.bot.scheduler import ThesisMaintenanceScheduler
    scheduler = ThesisMaintenanceScheduler(bot)
    scheduler.start()


def _start_drift_scheduler(bot: commands.Bot) -> None:
    from src.bot.scheduler import ThesisDriftScheduler
    scheduler = ThesisDriftScheduler(bot)
    scheduler.start()


def _start_snapshot_scheduler() -> None:
    from src.platform.bootstrap import get_snapshot_scheduler
    scheduler = get_snapshot_scheduler()
    scheduler.start()  # type: ignore[union-attr]


def _start_reminder_scheduler(bot: commands.Bot) -> None:
    from src.bot.scheduler import ReminderScheduler
    scheduler = ReminderScheduler(bot)
    scheduler.start()


def _start_decision_replay_scheduler(bot: commands.Bot) -> None:
    from src.bot.scheduler import DecisionReplayScheduler
    scheduler = DecisionReplayScheduler(bot)
    scheduler.start()
