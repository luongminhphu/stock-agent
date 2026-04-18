"""Market commands cog.

Owner: bot segment.
Commands:
    /quote <ticker>              — live quote for one ticker
    /quote_bulk <tickers>        — quotes for comma-separated tickers

No business logic. Delegates to QuoteService (market segment).
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands.base import BaseCog
from src.platform.bootstrap import get_quote_service
from src.platform.logging import get_logger

logger = get_logger(__name__)

_EXCHANGE_COLOUR = {
    "HOSE": discord.Color.from_rgb(255, 165, 0),   # orange
    "HNX":  discord.Color.from_rgb(0, 120, 215),   # blue
    "UPCOM": discord.Color.from_rgb(100, 180, 100), # green
}


class MarketCog(BaseCog):
    """Slash commands: /quote, /quote_bulk"""

    # ------------------------------------------------------------------
    # /quote
    # ------------------------------------------------------------------

    @app_commands.command(name="quote", description="Get live quote for a stock")
    @app_commands.describe(ticker="Stock ticker (e.g. HPG, VNM, FPT)")
    async def quote(
        self,
        interaction: discord.Interaction,
        ticker: str,
    ) -> None:
        await interaction.response.defer(ephemeral=False)  # visible to channel
        qs = get_quote_service()

        try:
            q = await qs.get_quote(ticker.upper())
        except Exception as exc:
            logger.error("quote.error", ticker=ticker, error=str(exc))
            await self.send_error(
                interaction,
                title="Quote not available",
                description=f"Could not fetch quote for **{ticker.upper()}**.\n`{exc}`",
            )
            return

        embed = _build_quote_embed(q)
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # /quote_bulk
    # ------------------------------------------------------------------

    @app_commands.command(name="quote_bulk", description="Get quotes for multiple tickers")
    @app_commands.describe(
        tickers="Comma-separated tickers (e.g. HPG,VNM,FPT — max 10)"
    )
    async def quote_bulk(
        self,
        interaction: discord.Interaction,
        tickers: str,
    ) -> None:
        await interaction.response.defer(ephemeral=False)

        ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()][:10]
        if not ticker_list:
            await self.send_error(
                interaction,
                title="No tickers provided",
                description="Please provide at least one ticker.",
            )
            return

        qs = get_quote_service()
        try:
            quotes = await qs.get_bulk_quotes(ticker_list)
        except Exception as exc:
            logger.error("quote_bulk.error", tickers=tickers, error=str(exc))
            await self.send_error(
                interaction,
                title="Bulk quote failed",
                description=f"Could not fetch quotes.\n`{exc}`",
            )
            return

        if not quotes:
            await self.send_error(
                interaction,
                title="No data",
                description="No quotes returned for those tickers.",
            )
            return

        lines = []
        for q in quotes:
            change_icon = "🔺" if q.change >= 0 else "🔻"
            ceiling_flag = " 🏆" if q.is_ceiling else ""
            floor_flag   = " 🚨" if q.is_floor   else ""
            lines.append(
                f"**{q.ticker}** {q.price:,.0f} VND "
                f"{change_icon}{q.change_pct:+.1f}% "
                f"Vol:{q.volume:,}{ceiling_flag}{floor_flag}"
            )

        embed = discord.Embed(
            title=f"📊 Quotes ({len(quotes)} tickers)",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Prices may be delayed · HOSE/HNX/UPCoM")
        await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# Embed builder
# ---------------------------------------------------------------------------


def _build_quote_embed(q: object) -> discord.Embed:  # type: ignore[type-arg]
    """Build a rich Discord embed for a single Quote."""
    change = getattr(q, "change", 0.0)
    change_pct = getattr(q, "change_pct", 0.0)
    is_ceiling = getattr(q, "is_ceiling", False)
    is_floor   = getattr(q, "is_floor", False)

    if is_ceiling:
        colour = discord.Color.from_rgb(180, 0, 200)  # purple — ceiling
        status = "🏆 Ceiling"
    elif is_floor:
        colour = discord.Color.from_rgb(50, 50, 50)   # dark — floor
        status = "🚨 Floor"
    elif change >= 0:
        colour = discord.Color.green()
        status = "🔺"
    else:
        colour = discord.Color.red()
        status = "🔻"

    ticker = getattr(q, "ticker", "?")
    price  = getattr(q, "price", 0.0)

    embed = discord.Embed(
        title=f"{status} {ticker}  {price:,.0f} VND",
        color=colour,
    )
    embed.add_field(name="Change",    value=f"{change:+,.0f} ({change_pct:+.2f}%)", inline=True)
    embed.add_field(name="Volume",    value=f"{getattr(q, 'volume', 0):,}",          inline=True)
    embed.add_field(name="Open",      value=f"{getattr(q, 'open', 0):,.0f}",         inline=True)
    embed.add_field(name="High",      value=f"{getattr(q, 'high', 0):,.0f}",         inline=True)
    embed.add_field(name="Low",       value=f"{getattr(q, 'low', 0):,.0f}",          inline=True)
    embed.add_field(name="Ref Price", value=f"{getattr(q, 'ref_price', 0):,.0f}",    inline=True)
    embed.add_field(name="Ceiling",   value=f"{getattr(q, 'ceiling', 0):,.0f}",      inline=True)
    embed.add_field(name="Floor",     value=f"{getattr(q, 'floor', 0):,.0f}",        inline=True)

    ts = getattr(q, "timestamp", None)
    ts_str = ts.strftime("%H:%M:%S %d/%m/%Y") if ts else "N/A"
    embed.set_footer(text=f"Last updated: {ts_str} · stock-agent")
    return embed
