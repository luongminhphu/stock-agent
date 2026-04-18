"""Watchlist commands cog.

Owner: bot segment.
Each command:
  1. Parses Discord input
  2. Calls WatchlistService (watchlist segment)
  3. Formats and returns Discord embed

No business logic here. All rules live in watchlist segment.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands.base import BaseCog
from src.watchlist.service import (
    AddToWatchlistInput,
    WatchlistItemAlreadyExistsError,
    WatchlistItemNotFoundError,
    WatchlistService,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)


class WatchlistCog(BaseCog):
    """Slash commands: /watchlist add|remove|list"""

    group = app_commands.Group(name="watchlist", description="Manage your watchlist")

    @group.command(name="add", description="Add a ticker to your watchlist")
    @app_commands.describe(
        ticker="Stock ticker (e.g. VNM)",
        note="Optional note about this position",
    )
    async def watchlist_add(
        self,
        interaction: discord.Interaction,
        ticker: str,
        note: str = "",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                svc = WatchlistService(session)
                await svc.add(AddToWatchlistInput(
                    user_id=user_id,
                    ticker=ticker.upper(),
                    note=note,
                ))
        except WatchlistItemAlreadyExistsError:
            await self.send_error(
                interaction,
                title="Already in watchlist",
                description=f"**{ticker.upper()}** is already in your watchlist.",
            )
            return
        except Exception as exc:
            logger.error("watchlist_add.error", ticker=ticker, error=str(exc))
            await self.send_error(interaction, title="Error", description=str(exc))
            return

        await self.send_ok(
            interaction,
            title="Added to watchlist",
            description=f"**{ticker.upper()}** has been added to your watchlist.",
        )

    @group.command(name="remove", description="Remove a ticker from your watchlist")
    @app_commands.describe(ticker="Stock ticker to remove")
    async def watchlist_remove(
        self,
        interaction: discord.Interaction,
        ticker: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                svc = WatchlistService(session)
                await svc.remove(user_id=user_id, ticker=ticker.upper())
        except WatchlistItemNotFoundError:
            await self.send_error(
                interaction,
                title="Not found",
                description=f"**{ticker.upper()}** is not in your watchlist.",
            )
            return
        except Exception as exc:
            logger.error("watchlist_remove.error", ticker=ticker, error=str(exc))
            await self.send_error(interaction, title="Error", description=str(exc))
            return

        await self.send_ok(
            interaction,
            title="Removed",
            description=f"**{ticker.upper()}** removed from your watchlist.",
        )

    @group.command(name="list", description="Show your watchlist")
    async def watchlist_list(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                svc = WatchlistService(session)
                items = await svc.list_items(user_id)
        except Exception as exc:
            logger.error("watchlist_list.error", error=str(exc))
            await self.send_error(interaction, title="Error", description=str(exc))
            return

        if not items:
            await self.send_ok(
                interaction,
                title="Your watchlist",
                description="Your watchlist is empty. Use `/watchlist add <ticker>` to start.",
            )
            return

        lines = []
        for item in items:
            note_part = f" \u2014 {item.note}" if item.note else ""
            lines.append(f"\u2022 **{item.ticker}**{note_part}")

        embed = discord.Embed(
            title="\U0001f4cb Your Watchlist",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"{len(items)} ticker(s)")
        await interaction.followup.send(embed=embed, ephemeral=True)
