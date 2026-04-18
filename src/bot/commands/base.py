"""Base cog with shared helpers for all command cogs.

Owner: bot segment.
Provides:
  - db_session()    async context manager for DB sessions
  - user_id()       extract Discord user ID string
  - send_ok()       success embed shortcut
  - send_error()    error embed shortcut
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import discord
from discord.ext import commands

from src.platform.db import AsyncSessionLocal


class BaseCog(commands.Cog):
    """Shared utilities for all command cogs."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # DB helper
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def db_session(self) -> AsyncGenerator:  # type: ignore[type-arg]
        """Async context manager: yield session, auto-commit on success, rollback on error."""
        async with AsyncSessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # ------------------------------------------------------------------
    # Discord helpers
    # ------------------------------------------------------------------

    @staticmethod
    def user_id(interaction: discord.Interaction) -> str:
        return str(interaction.user.id)

    @staticmethod
    async def send_ok(
        interaction: discord.Interaction,
        title: str,
        description: str,
    ) -> None:
        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @staticmethod
    async def send_error(
        interaction: discord.Interaction,
        title: str,
        description: str,
    ) -> None:
        embed = discord.Embed(
            title=f"❌ {title}",
            description=description,
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
