"""Base cog with shared helpers for all command cogs.

Owner: bot segment.
Provides:
  - db_session()        async context manager for DB sessions
  - user_id()           extract Discord user ID string
  - send_ok()           success embed shortcut
  - send_error()        error embed shortcut
  - send_info()         informational embed (blue)
  - send_warning()      warning embed (orange)
  - send_paginated()    truncate + show count footer for long lists
  - fmt_vnd()           format VND price: 50_000 → "50,000 VND"
  - fmt_pct()           format percentage: 0.052 → "+5.2%" or "-5.2%"
  - fmt_rr()            format risk/reward ratio: 2.5 → "2.50x"
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator, Sequence

import discord
from discord.ext import commands

from src.platform.db import AsyncSessionLocal


_MAX_EMBED_DESC = 4096  # Discord limit


class BaseCog(commands.Cog):
    """Shared utilities for all command cogs."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # DB helper
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def db_session(self) -> AsyncGenerator:
        async with AsyncSessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # ------------------------------------------------------------------
    # Discord interaction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def user_id(interaction: discord.Interaction) -> str:
        return str(interaction.user.id)

    @staticmethod
    async def defer(interaction: discord.Interaction, *, ephemeral: bool = True) -> None:
        """Defer response. Centralises ephemeral default (True = private)."""
        await interaction.response.defer(ephemeral=ephemeral)

    # ------------------------------------------------------------------
    # Embed shortcuts
    # ------------------------------------------------------------------

    @staticmethod
    async def send_ok(
        interaction: discord.Interaction,
        title: str,
        description: str,
        *,
        ephemeral: bool = True,
    ) -> None:
        embed = discord.Embed(title=title, description=description, color=discord.Color.green())
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    @staticmethod
    async def send_error(
        interaction: discord.Interaction,
        title: str,
        description: str,
        *,
        ephemeral: bool = True,
    ) -> None:
        embed = discord.Embed(
            title=f"❌ {title}",
            description=description,
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    @staticmethod
    async def send_info(
        interaction: discord.Interaction,
        title: str,
        description: str,
        *,
        ephemeral: bool = True,
        footer: str | None = None,
    ) -> None:
        """Informational embed (blue). Use for neutral results, lists, scans."""
        embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
        if footer:
            embed.set_footer(text=footer)
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    @staticmethod
    async def send_warning(
        interaction: discord.Interaction,
        title: str,
        description: str,
        *,
        ephemeral: bool = True,
    ) -> None:
        """Warning embed (orange). Use for partial results or rate-limit notices."""
        embed = discord.Embed(
            title=f"⚠️ {title}",
            description=description,
            color=discord.Color.orange(),
        )
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    # ------------------------------------------------------------------
    # Pagination helper
    # ------------------------------------------------------------------

    @staticmethod
    def paginate_lines(
        lines: Sequence[str],
        *,
        max_items: int = 20,
        max_chars: int = _MAX_EMBED_DESC,
    ) -> tuple[str, str]:
        """Return (body_text, footer_hint).

        Truncates *lines* to fit Discord embed limits.
        footer_hint is non-empty when truncation happened.
        """
        shown = lines[:max_items]
        body = "\n".join(shown)
        if len(body) > max_chars:
            body = body[:max_chars - 3] + "..."
        hidden = len(lines) - len(shown)
        footer = f"Showing {len(shown)} of {len(lines)}" if hidden > 0 else ""
        return body, footer

    # ------------------------------------------------------------------
    # Formatting helpers  (no business logic — pure string formatting)
    # ------------------------------------------------------------------

    @staticmethod
    def fmt_vnd(price: float | int | None, *, fallback: str = "N/A") -> str:
        """Format VND price: 50_000 → '50,000 VND'."""
        if price is None:
            return fallback
        return f"{price:,.0f} VND"

    @staticmethod
    def fmt_pct(value: float | None, *, fallback: str = "N/A") -> str:
        """Format percentage change: 0.052 → '+5.2%', -0.03 → '-3.0%'."""
        if value is None:
            return fallback
        sign = "+" if value >= 0 else ""
        return f"{sign}{value:.1f}%"

    @staticmethod
    def fmt_rr(rr: float | None, *, fallback: str = "N/A") -> str:
        """Format risk/reward: 2.5 → '2.50x'."""
        if rr is None:
            return fallback
        return f"{rr:.2f}x"
