"""Base cog with shared helpers for all command cogs.

Owner: bot segment.
Provides DB session injection and user_id resolution.
No business logic — only adapter plumbing.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import discord
from discord.ext import commands
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.db import AsyncSessionLocal
from src.platform.logging import get_logger

logger = get_logger(__name__)


class BaseCog(commands.Cog):
    """Base class for all stock-agent cogs.

    Provides:
        db_session()  — async context manager yielding an AsyncSession
        user_id()     — extract stable user identifier from interaction
        send_error()  — standardised error embed reply
        send_ok()     — standardised success embed reply
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @asynccontextmanager
    async def db_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Provide a DB session for the duration of a command handler."""
        async with AsyncSessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @staticmethod
    def user_id(interaction: discord.Interaction) -> str:
        """Return a stable string user ID from a Discord interaction."""
        return str(interaction.user.id)

    @staticmethod
    async def send_error(
        interaction: discord.Interaction,
        title: str,
        description: str,
    ) -> None:
        embed = discord.Embed(
            title=f"\u274c {title}",
            description=description,
            color=discord.Color.red(),
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @staticmethod
    async def send_ok(
        interaction: discord.Interaction,
        title: str,
        description: str,
    ) -> None:
        embed = discord.Embed(
            title=f"\u2705 {title}",
            description=description,
            color=discord.Color.green(),
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
