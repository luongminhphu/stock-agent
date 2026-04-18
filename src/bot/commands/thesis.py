"""Thesis commands cog.

Owner: bot segment.
Commands: /thesis add | list | close | invalidate

All business rules live in thesis segment.
This cog only parses input, calls ThesisService, formats output.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands.base import BaseCog
from src.thesis.service import (
    CreateThesisInput,
    ThesisAlreadyClosedError,
    ThesisNotFoundError,
    ThesisService,
)
from src.thesis.models import ThesisStatus
from src.platform.logging import get_logger

logger = get_logger(__name__)


class ThesisCog(BaseCog):
    """Slash commands: /thesis add|list|close|invalidate"""

    group = app_commands.Group(name="thesis", description="Manage your investment theses")

    @group.command(name="add", description="Create a new investment thesis")
    @app_commands.describe(
        ticker="Stock ticker (e.g. HPG)",
        title="Short thesis title",
        summary="Brief thesis summary",
        entry="Entry price in VND (optional)",
        target="Target price in VND (optional)",
        stop="Stop-loss price in VND (optional)",
    )
    async def thesis_add(
        self,
        interaction: discord.Interaction,
        ticker: str,
        title: str,
        summary: str = "",
        entry: float | None = None,
        target: float | None = None,
        stop: float | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                svc = ThesisService(session)
                thesis = await svc.create(CreateThesisInput(
                    user_id=user_id,
                    ticker=ticker.upper(),
                    title=title,
                    summary=summary,
                    entry_price=entry,
                    target_price=target,
                    stop_loss=stop,
                ))
        except Exception as exc:
            logger.error("thesis_add.error", ticker=ticker, error=str(exc))
            await self.send_error(interaction, title="Error", description=str(exc))
            return

        desc_parts = [f"**{thesis.ticker}** \u2014 {thesis.title}"]
        if thesis.upside_pct is not None:
            desc_parts.append(f"Upside: **{thesis.upside_pct:.1f}%**")
        if thesis.risk_reward is not None:
            desc_parts.append(f"R/R: **{thesis.risk_reward:.1f}x**")

        await self.send_ok(
            interaction,
            title="Thesis created",
            description="\n".join(desc_parts),
        )

    @group.command(name="list", description="List your active theses")
    async def thesis_list(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                svc = ThesisService(session)
                theses = await svc.list_for_user(user_id, status=ThesisStatus.ACTIVE)
        except Exception as exc:
            logger.error("thesis_list.error", error=str(exc))
            await self.send_error(interaction, title="Error", description=str(exc))
            return

        if not theses:
            await self.send_ok(
                interaction,
                title="Your theses",
                description="No active theses. Use `/thesis add` to create one.",
            )
            return

        lines = []
        for t in theses:
            score_part = f" | Score: {t.score:.0f}" if t.score is not None else ""
            lines.append(f"\u2022 **#{t.id} {t.ticker}** \u2014 {t.title}{score_part}")

        embed = discord.Embed(
            title="\U0001f4d1 Active Theses",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"{len(theses)} active thesis(es)")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @group.command(name="close", description="Close a thesis")
    @app_commands.describe(thesis_id="Thesis ID to close")
    async def thesis_close(
        self,
        interaction: discord.Interaction,
        thesis_id: int,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                svc = ThesisService(session)
                await svc.close(thesis_id=thesis_id, user_id=user_id)
        except ThesisNotFoundError:
            await self.send_error(interaction, title="Not found", description=f"Thesis #{thesis_id} not found.")
            return
        except ThesisAlreadyClosedError as exc:
            await self.send_error(interaction, title="Already closed", description=str(exc))
            return
        except Exception as exc:
            logger.error("thesis_close.error", thesis_id=thesis_id, error=str(exc))
            await self.send_error(interaction, title="Error", description=str(exc))
            return

        await self.send_ok(interaction, title="Thesis closed", description=f"Thesis #{thesis_id} has been closed.")

    @group.command(name="invalidate", description="Mark a thesis as invalidated")
    @app_commands.describe(thesis_id="Thesis ID to invalidate")
    async def thesis_invalidate(
        self,
        interaction: discord.Interaction,
        thesis_id: int,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                svc = ThesisService(session)
                await svc.invalidate(thesis_id=thesis_id, user_id=user_id)
        except ThesisNotFoundError:
            await self.send_error(interaction, title="Not found", description=f"Thesis #{thesis_id} not found.")
            return
        except ThesisAlreadyClosedError as exc:
            await self.send_error(interaction, title="Already closed", description=str(exc))
            return
        except Exception as exc:
            logger.error("thesis_invalidate.error", thesis_id=thesis_id, error=str(exc))
            await self.send_error(interaction, title="Error", description=str(exc))
            return

        await self.send_ok(interaction, title="Thesis invalidated", description=f"Thesis #{thesis_id} marked as invalidated.")
