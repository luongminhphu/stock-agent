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
        help_command=None,  # We use slash commands; disable legacy help
    )

    @bot.event
    async def on_ready() -> None:
        await bootstrap()
        await _register_cogs(bot)
        await bot.tree.sync()
        logger.info(
            "bot.ready",
            user=str(bot.user),
            guild_count=len(bot.guilds),
        )

    @bot.event
    async def on_error(event: str, *args: object, **kwargs: object) -> None:
        logger.error("bot.event_error", event=event)

    return bot


async def _register_cogs(bot: commands.Bot) -> None:
    """Load all command cogs. Add new cogs here only — no logic."""
    from src.bot.commands.watchlist import WatchlistCog
    from src.bot.commands.thesis import ThesisCog
    from src.bot.commands.market import MarketCog

    await bot.add_cog(WatchlistCog(bot))
    await bot.add_cog(ThesisCog(bot))
    await bot.add_cog(MarketCog(bot))
    logger.info("bot.cogs_loaded", cogs=["WatchlistCog", "ThesisCog", "MarketCog"])


def run() -> None:
    """Entry point — called from __main__ or a process manager."""
    bot = create_bot()
    bot.run(settings.discord_token, log_handler=None)  # structlog handles logging
