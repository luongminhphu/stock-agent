"""Market commands cog.

Owner: bot segment.
Commands: /quote <ticker>

Wave 1: returns a stub response (no adapter wired yet).
Wave 2: inject QuoteService with real adapter.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands.base import BaseCog
from src.market.registry import SymbolNotFoundError, registry
from src.market.quote_service import QuoteServiceNotConfiguredError
from src.platform.logging import get_logger

logger = get_logger(__name__)


class MarketCog(BaseCog):
    """Slash commands: /quote"""

    @app_commands.command(name="quote", description="Get a quick quote for a ticker")
    @app_commands.describe(ticker="Stock ticker (e.g. VNM, HPG, FPT)")
    async def quote(
        self,
        interaction: discord.Interaction,
        ticker: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        ticker = ticker.upper()

        # Validate ticker exists in registry
        try:
            info = registry.resolve(ticker)
        except SymbolNotFoundError:
            await self.send_error(
                interaction,
                title="Unknown ticker",
                description=(
                    f"**{ticker}** was not found in the registry.\n"
                    "Check the ticker and try again."
                ),
            )
            return

        # Wave 1: QuoteService not wired yet
        # Wave 2: inject QuoteService and call get_quote(ticker)
        embed = discord.Embed(
            title=f"\U0001f4c8 {ticker} \u2014 {info.name}",
            description=(
                f"Exchange: **{info.exchange.value}** | Sector: **{info.sector.value}**\n"
                f"\u26a0\ufe0f Live quote not available yet (Wave 2)."
            ),
            color=discord.Color.light_grey(),
        )
        embed.set_footer(text="stock-agent \u2022 Wave 1 stub")
        await interaction.followup.send(embed=embed, ephemeral=True)
