"""Base cog — shared utilities for all command cogs.

Owner: bot segment.

All embed building, formatting, and safe-send primitives are provided by
``src.bot.discord_helper``. This file only provides the BaseCog class with
thin wrappers so command cogs can call ``self.send_ok(...)`` as a convenience
without importing discord_helper themselves.

Public surface:
    db_session()        async context manager for DB sessions (auto-commit/rollback)
    user_id()           extract Discord user ID string from interaction
    defer()             defer interaction response (delegates to safe_defer)
    send_ok()           success embed shortcut
    send_error()        error embed shortcut
    send_info()         informational embed (blue)
    send_warning()      warning embed (orange)
    paginate_lines()    truncate + build footer hint for long line lists
    fmt_vnd()           VND compact format: 1_500_000 → '1.50M'
    fmt_vnd_full()      VND full format: 50_000 → '50,000 VND'
    fmt_pct()           ratio → percentage: 0.052 → '+5.2%'
    fmt_pct_direct()    already-multiplied pct: 5.2 → '+5.2%'
    fmt_rr()            risk/reward: 2.5 → '2.50x'
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager

import discord
from discord.ext import commands

from src.platform.db import AsyncSessionLocal
from src.bot.discord_helper import (
    safe_defer,
    safe_followup,
    send_ok       as _send_ok,
    send_error    as _send_error,
    send_info     as _send_info,
    send_warning  as _send_warning,
    paginate_lines,
    fmt_vnd,
    fmt_vnd_full,
    fmt_pct,
    fmt_pct_direct,
    fmt_rr,
)


class BaseCog(commands.Cog):
    """Shared utilities for all command cogs.

    All embed / formatting / send logic delegates to discord_helper.
    Add no business logic here — keep this as a pure adapter shim.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ──────────────────────────────────────────────────────────────
    # DB helper
    # ──────────────────────────────────────────────────────────────

    @asynccontextmanager
    async def db_session(self) -> AsyncGenerator:
        """Async context manager for DB sessions with auto-commit/rollback."""
        async with AsyncSessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # ──────────────────────────────────────────────────────────────
    # Interaction helpers
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def user_id(interaction: discord.Interaction) -> str:
        """Return the interaction user's Discord ID as a string."""
        return str(interaction.user.id)

    @staticmethod
    async def defer(
        interaction: discord.Interaction,
        *,
        ephemeral: bool = True,
        thinking: bool = True,
    ) -> bool:
        """Defer the interaction response. Returns True on success.

        Delegates to discord_helper.safe_defer — never raises.
        """
        return await safe_defer(interaction, ephemeral=ephemeral, thinking=thinking)

    # ──────────────────────────────────────────────────────────────
    # Embed shortcuts (delegate to discord_helper module functions)
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    async def send_ok(
        interaction: discord.Interaction,
        title: str,
        description: str = "",
        *,
        ephemeral: bool = True,
        context: str | None = None,
    ) -> None:
        """Send a green success embed as followup."""
        await _send_ok(interaction, title, description, ephemeral=ephemeral, context=context)

    @staticmethod
    async def send_error(
        interaction: discord.Interaction,
        title: str = "❌ Lỗi",
        description: str = "Đã xảy ra lỗi không mong muốn.",
        *,
        ephemeral: bool = True,
        context: str | None = None,
    ) -> None:
        """Send a red error embed as followup."""
        await _send_error(interaction, title, description, ephemeral=ephemeral, context=context)

    @staticmethod
    async def send_info(
        interaction: discord.Interaction,
        title: str,
        description: str = "",
        *,
        ephemeral: bool = True,
        context: str | None = None,
        view: discord.ui.View | None = None,
    ) -> None:
        """Send a blue informational embed as followup."""
        await _send_info(interaction, title, description, ephemeral=ephemeral, context=context, view=view)

    @staticmethod
    async def send_warning(
        interaction: discord.Interaction,
        title: str,
        description: str = "",
        *,
        ephemeral: bool = True,
        context: str | None = None,
    ) -> None:
        """Send an orange warning embed as followup."""
        await _send_warning(interaction, title, description, ephemeral=ephemeral, context=context)

    # ──────────────────────────────────────────────────────────────
    # Pagination helper
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def paginate_lines(
        lines: Sequence[str],
        *,
        max_items: int = 20,
        max_chars: int = 4096,
    ) -> tuple[str, str]:
        """Return (body_text, footer_hint) for a list of lines.

        Delegates to discord_helper.paginate_lines.
        footer_hint is non-empty when truncation occurred.
        """
        return paginate_lines(lines, max_items=max_items, max_chars=max_chars)

    # ──────────────────────────────────────────────────────────────
    # Formatting helpers — pure string formatting, no business logic
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def fmt_vnd(price: float | int | None, decimals: int = 0) -> str:
        """Format VND with K/M/B suffix: 1_500_000 → '1.50M'.

        CONTRACT: price is a raw VND integer.
        Delegates to discord_helper.fmt_vnd.
        """
        return fmt_vnd(price, decimals)

    @staticmethod
    def fmt_vnd_full(price: float | int | None) -> str:
        """Format VND with full comma grouping + suffix: 50_000 → '50,000 VND'.

        Delegates to discord_helper.fmt_vnd_full.
        """
        return fmt_vnd_full(price)

    @staticmethod
    def fmt_pct(
        value: float | None,
        decimals: int = 1,
        *,
        sign: bool = True,
    ) -> str:
        """Format a decimal RATIO as percentage: 0.052 → '+5.2%'.

        CONTRACT: value is a ratio (0.052 = 5.2%). NOT already-multiplied.
        Delegates to discord_helper.fmt_pct.
        """
        return fmt_pct(value, decimals, sign=sign)

    @staticmethod
    def fmt_pct_direct(
        value: float | None,
        decimals: int = 1,
        *,
        sign: bool = True,
    ) -> str:
        """Format an already-multiplied percentage: 5.2 → '+5.2%'.

        Use when the source value is already a percentage, not a ratio.
        Delegates to discord_helper.fmt_pct_direct.
        """
        return fmt_pct_direct(value, decimals, sign=sign)

    @staticmethod
    def fmt_rr(rr: float | None, *, fallback: str = "N/A") -> str:
        """Format risk/reward ratio: 2.5 → '2.50x'.

        Delegates to discord_helper.fmt_rr.
        """
        return fmt_rr(rr, fallback=fallback)
