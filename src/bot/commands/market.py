"""Market commands cog.

Owner: bot segment.
Commands: /quote <ticker>

Wires QuoteService from platform bootstrap.
No business logic — parse input → call service → format embed.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands.base import BaseCog
from src.market.registry import SymbolNotFoundError, registry
from src.market.quote_service import Quote
from src.platform.bootstrap import get_quote_service
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Colour coding: green = up, red = down, grey = flat
_COLOUR_UP = discord.Color.green()
_COLOUR_DOWN = discord.Color.red()
_COLOUR_FLAT = discord.Color.light_grey()
_COLOUR_CEILING = discord.Color.from_rgb(255, 165, 0)   # orange — at ceiling
_COLOUR_FLOOR = discord.Color.from_rgb(128, 0, 128)      # purple — at floor


class MarketCog(BaseCog):
    """Slash commands: /quote"""

    @app_commands.command(name="quote", description="Get a live quote for a ticker")
    @app_commands.describe(ticker="Stock ticker (e.g. VNM, HPG, FPT)")
    async def quote(
        self,
        interaction: discord.Interaction,
        ticker: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        ticker = ticker.upper()

        # 1. Validate ticker in registry (fast, no network)
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

        # 2. Fetch live quote via ChainedAdapter (VCI → VNDirect)
        try:
            quote_svc = get_quote_service()
            q = await quote_svc.get_quote(ticker)
        except Exception as exc:
            logger.error("bot.quote.error", ticker=ticker, error=str(exc))
            await self.send_error(
                interaction,
                title="Market data unavailable",
                description=(
                    f"Could not fetch live quote for **{ticker}**.\n"
                    f"Error: `{exc}`"
                ),
            )
            return

        # 3. Format and send embed
        embed = _build_quote_embed(q, info.name, info.exchange.value)
        await interaction.followup.send(embed=embed, ephemeral=True)


def _build_quote_embed(
    q: Quote,
    name: str,
    exchange: str,
) -> discord.Embed:
    """Build a rich Discord embed from a Quote."""
    # Colour by market status
    if q.is_ceiling:
        colour = _COLOUR_CEILING
        status_icon = "\U0001f7e0"  # orange circle — ceiling
    elif q.is_floor:
        colour = _COLOUR_FLOOR
        status_icon = "\U0001f7e3"  # purple circle — floor
    elif q.is_up:
        colour = _COLOUR_UP
        status_icon = "\U0001f7e2"  # green circle
    elif q.is_down:
        colour = _COLOUR_DOWN
        status_icon = "\U0001f534"  # red circle
    else:
        colour = _COLOUR_FLAT
        status_icon = "\u26aa"  # grey circle — flat

    embed = discord.Embed(
        title=f"{status_icon} {q.ticker} — {name}",
        colour=colour,
    )

    # Price row
    embed.add_field(
        name="Price",
        value=f"**{q.format_price()}** VND",
        inline=True,
    )
    embed.add_field(
        name="Change",
        value=q.format_change(),
        inline=True,
    )
    embed.add_field(
        name="Volume",
        value=f"{q.volume:,}",
        inline=True,
    )

    # OHLC row
    embed.add_field(name="Open",  value=f"{q.open:,.0f}",  inline=True)
    embed.add_field(name="High",  value=f"{q.high:,.0f}",  inline=True)
    embed.add_field(name="Low",   value=f"{q.low:,.0f}",   inline=True)

    # Ref / Ceiling / Floor row
    embed.add_field(name="Ref",     value=f"{q.ref_price:,.0f}", inline=True)
    embed.add_field(name="Ceiling", value=f"{q.ceiling:,.0f}",   inline=True)
    embed.add_field(name="Floor",   value=f"{q.floor:,.0f}",     inline=True)

    embed.set_footer(
        text=(
            f"{exchange} • "
            f"{q.timestamp.strftime('%H:%M:%S %d/%m/%Y') if q.timestamp else 'N/A'} "
            "• stock-agent"
        )
    )
    return embed
