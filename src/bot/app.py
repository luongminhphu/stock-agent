"""Discord bot application factory.

Owner: bot segment.
This module only wires the runtime — no business logic here.
All domain operations are delegated to segment services.
"""

from __future__ import annotations

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
        _start_snapshot_scheduler()
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


async def _sync_tree(bot: commands.Bot) -> None:
    """Sync slash commands.

    Guild sync = instant (dùng khi DISCORD_GUILD_ID được set).
    Global sync = ~1 giờ để Discord propagate (fallback khi không có guild ID).
    """
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
    from src.bot.commands.help import HelpCog
    from src.bot.commands.market import MarketCog
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
    logger.info(
        "bot.cogs_loaded",
        cogs=[
            "WatchlistCog",
            "ThesisCrudCog",
            "ThesisReviewCog",
            "MarketCog",
            "BriefingCog",
            "HelpCog",
            "WhyCog",
        ],
    )


def _start_briefing_scheduler(bot: commands.Bot) -> None:
    """Attach BriefingScheduler if all three channel/user settings are configured."""
    if not settings.briefing_scheduler_enabled:
        logger.info(
            "bot.briefing_scheduler.skipped",
            reason="morning_channel_id/eod_channel_id/scheduler_user_id not configured",
        )
        return

    from src.bot.scheduler import BriefingScheduler

    scheduler = BriefingScheduler(client=bot)
    scheduler.start()
    logger.info(
        "bot.briefing_scheduler.started",
        morning_channel=settings.morning_channel_id,
        eod_channel=settings.eod_channel_id,
        user=settings.scheduler_user_id,
    )


def _start_scan_scheduler(bot: commands.Bot) -> None:
    """Attach WatchlistScanScheduler if required settings are configured."""
    if settings.environment == "test":
        logger.info("bot.scan_scheduler.skipped", reason="test environment")
        return

    user_id = getattr(settings, "scheduler_user_id", None)
    channel_id = getattr(settings, "morning_channel_id", None)
    if not user_id or not channel_id:
        logger.info(
            "bot.scan_scheduler.skipped",
            reason="scheduler_user_id or morning_channel_id not configured",
        )
        return

    from src.bot.scheduler import WatchlistScanScheduler

    scheduler = WatchlistScanScheduler(client=bot)
    scheduler.start()
    logger.info(
        "bot.scan_scheduler.started",
        interval_minutes=5,
        channel=channel_id,
        user=user_id,
    )


def _start_snapshot_scheduler() -> None:
    """Start the daily thesis snapshot job (market segment).

    Runs at 15:10 ICT on weekdays regardless of briefing config.
    Guarded: skips gracefully in test/mock environment.
    """
    from src.platform.config import settings

    if settings.environment == "test":
        logger.info("bot.snapshot_scheduler.skipped", reason="test environment")
        return

    from src.market.snapshot_scheduler import SnapshotScheduler
    from src.platform.bootstrap import get_snapshot_scheduler

    scheduler: SnapshotScheduler = get_snapshot_scheduler()  # type: ignore[assignment]
    scheduler.start()
    logger.info("bot.snapshot_scheduler.started", time_ict="15:10")


def run() -> None:
    """Entry point — called from __main__ or a process manager."""
    bot = create_bot()
    bot.run(settings.discord_token, log_handler=None)
