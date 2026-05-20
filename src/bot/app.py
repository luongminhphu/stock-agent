"""Discord bot application factory.

Owner: bot segment.
This module only wires the runtime — no business logic here.
All domain operations are delegated to segment services.
"""

from __future__ import annotations

import asyncio
import traceback as tb

import discord
from discord.ext import commands

from src.platform.bootstrap import bootstrap, shutdown
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
        # Guard: set flag FIRST before any work so that gateway reconnects
        # never retry a failing init loop (would cause duplicate cog errors
        # and empty tree syncs wiping all slash commands from Discord).
        if getattr(bot, "_stock_agent_ready", False):
            logger.warning(
                "bot.on_ready.skip",
                reason="already_initialized",
                user=str(bot.user),
            )
            return
        bot._stock_agent_ready = True  # type: ignore[attr-defined]

        try:
            await bootstrap()
            _inject_briefing_listener(bot)  # Wave 8: inject discord.Client after login
            await _register_cogs(bot)

            # Sync tree immediately after cogs are loaded — before schedulers start.
            # This ensures slash commands are registered even if a scheduler raises.
            await _sync_tree(bot)

            _start_briefing_scheduler(bot)
            _start_scan_scheduler(bot)
            _start_thesis_maintenance_scheduler(bot)
            _start_drift_scheduler(bot)
            _start_snapshot_scheduler()
            _start_reminder_scheduler(bot)
            _start_decision_replay_scheduler(bot)
            _start_memory_consolidator_scheduler(bot)
            _start_recommendation_listener(bot)  # Wave 4: event-driven alerts
            _start_opportunity_screen_scheduler(bot)  # Wave 3: sector rotation 09:10 ICT
            logger.info(
                "bot.ready",
                user=str(bot.user),
                guild_count=len(bot.guilds),
            )
        except Exception as exc:
            # Log full traceback so the real root cause is never swallowed
            # by the structured JSON logger truncating the message field.
            logger.exception(
                "bot.on_ready.failed",
                error=str(exc),
                traceback=tb.format_exc(),
            )
            raise

    @bot.event
    async def on_close() -> None:
        """Called by discord.py when the bot disconnects / process exits.

        Gives bootstrap.shutdown() a chance to close httpx clients and other
        resources before the event loop is torn down.
        """
        await shutdown()
        logger.info("bot.closed")

    @bot.event
    async def on_error(event: str, *args: object, **kwargs: object) -> None:
        logger.error(
            "bot.event_error",
            event_name=event,
            traceback=tb.format_exc(),
        )

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
    from src.bot.commands.sector_rotation import SectorRotationCog
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
    await bot.add_cog(SectorRotationCog(bot))
    logger.info(
        "bot.cogs_loaded",
        cogs=[
            "WatchlistCog", "ThesisCrudCog", "ThesisReviewCog", "MarketCog",
            "BriefingCog", "HelpCog", "WhyCog", "PretradeCog",
            "ConvictionTimelineCog", "PortfolioCog", "StressTestCog",
            "DecisionCog", "SchedulerTriggerCog", "HealthCog", "SectorRotationCog",
        ],
    )


def _inject_briefing_listener(bot: commands.Bot) -> None:
    """Inject discord.Client into BriefingListener after bot login.

    bootstrap() registers BriefingListener on the event bus but cannot
    pass discord.Client (bot hasn't logged in yet at bootstrap time).
    This call completes the wiring immediately after on_ready fires.
    """
    from src.platform.bootstrap import get_briefing_listener
    listener = get_briefing_listener()
    if listener is not None:
        listener.set_client(bot)
        logger.info("bot.briefing_listener.client_injected")
    else:
        logger.error(
            "bot.briefing_listener.not_available",
            reason="scheduler_user_id not configured — BriefingListener skipped at bootstrap. "
                   "Morning and EOD briefs will NOT be delivered. "
                   "Set SCHEDULER_USER_ID in env to enable automatic briefings.",
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
    if scheduler is None:
        logger.warning(
            "bot.snapshot_scheduler.not_available",
            reason="get_snapshot_scheduler() returned None — scheduler_user_id may not be configured",
        )
        return
    scheduler.start()


def _start_reminder_scheduler(bot: commands.Bot) -> None:
    from src.bot.scheduler import ReminderScheduler
    scheduler = ReminderScheduler(bot)
    scheduler.start()


def _start_decision_replay_scheduler(bot: commands.Bot) -> None:
    from src.bot.scheduler import DecisionReplayScheduler
    scheduler = DecisionReplayScheduler(bot)
    scheduler.start()


def _start_memory_consolidator_scheduler(bot: commands.Bot) -> None:
    """Wire MemoryConsolidatorScheduler — Sundays 09:00 ICT.

    Returns early with a warning if MemoryConsolidator was not initialised
    at bootstrap (scheduler_user_id not configured). Non-blocking.
    """
    from src.platform.bootstrap import get_memory_consolidator
    from src.bot.scheduler import MemoryConsolidatorScheduler

    if get_memory_consolidator() is None:
        logger.warning(
            "bot.memory_consolidator_scheduler.not_available",
            reason="MemoryConsolidator not initialised — scheduler_user_id may not be configured",
        )
        return

    scheduler = MemoryConsolidatorScheduler(bot)
    scheduler.start()


def _start_recommendation_listener(bot: commands.Bot) -> None:
    """Wire Wave 4: RecommendationListener subscribes event bus → Discord push."""
    from src.bot.recommendation_listener import RecommendationListener
    listener = RecommendationListener(bot)
    listener.register()


def _start_opportunity_screen_scheduler(bot: commands.Bot) -> None:
    """Wire Wave 3: OpportunityScreenScheduler → sector rotation at 09:10 ICT daily.

    bootstrap() initialises the singleton and registers OpportunityScreenSubscriber
    on the event bus, but scheduler.start() must be called here (after bot login)
    because discord.ext.tasks requires the bot event loop to be running.

    Also injects discord.Client into the subscriber so it can deliver
    sector rotation output to the morning Discord channel.
    """
    from src.platform.bootstrap import (
        get_opportunity_screen_scheduler,
        get_opportunity_screen_subscriber,
    )

    scheduler = get_opportunity_screen_scheduler()
    if scheduler is None:
        logger.warning(
            "bot.opportunity_screen_scheduler.not_available",
            reason="bootstrap did not initialise OpportunityScreenScheduler",
        )
        return

    scheduler.start()
    logger.info("bot.opportunity_screen_scheduler.started")

    # Inject discord.Client so subscriber can post to morning channel
    subscriber = get_opportunity_screen_subscriber()
    if subscriber is not None and hasattr(subscriber, "set_client"):
        subscriber.set_client(bot)
        logger.info("bot.opportunity_screen_subscriber.client_injected")
