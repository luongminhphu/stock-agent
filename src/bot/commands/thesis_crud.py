"""Thesis CRUD commands cog.

Owner: bot segment.
Commands:
    /thesis add    — create a new investment thesis
    /thesis list   — list your theses
    /thesis close  — close or invalidate a thesis

Adapter only: parse input → call ThesisService → format embed.
No business logic.
"""

from __future__ import annotations

import discord
from discord import app_commands

from src.bot.commands.base import BaseCog
from src.bot.commands.thesis_embeds import STATUS_ICON
from src.platform.logging import get_logger
from src.thesis.models import ThesisStatus
from src.thesis.service import CreateThesisInput, ThesisNotFoundError, ThesisService

logger = get_logger(__name__)


class ThesisCrudCog(BaseCog):
    """Slash commands: /thesis add, /thesis list, /thesis close."""

    group = app_commands.Group(
        name="thesis",
        description="Manage your investment theses",
    )

    # ------------------------------------------------------------------
    # /thesis add
    # ------------------------------------------------------------------

    @group.command(name="add", description="Create a new investment thesis")
    @app_commands.describe(
        ticker="Stock ticker (e.g. HPG, VNM)",
        title="Short title for the thesis",
        entry_price="Your entry price in VND (e.g. 50000)",
        target_price="Target price in VND",
        stop_loss="Stop-loss price in VND",
        summary="Optional thesis summary",
    )
    async def thesis_add(
        self,
        interaction: discord.Interaction,
        ticker: str,
        title: str,
        entry_price: float,
        target_price: float,
        stop_loss: float,
        summary: str = "",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                svc = ThesisService(session)
                thesis = await svc.create(
                    CreateThesisInput(
                        user_id=user_id,
                        ticker=ticker.upper(),
                        title=title,
                        summary=summary,
                        entry_price=entry_price,
                        target_price=target_price,
                        stop_loss=stop_loss,
                    )
                )
        except Exception as exc:
            logger.error("thesis_add.error", ticker=ticker, error=str(exc))
            await self.send_error(
                interaction,
                title="Failed to create thesis",
                description=str(exc),
            )
            return

        upside = thesis.upside_pct
        rr = thesis.risk_reward
        upside_str = f"+{upside:.1f}%" if upside is not None else "N/A"
        rr_str = f"{rr:.2f}x" if rr is not None else "N/A"

        embed = discord.Embed(
            title=f"✅ Thesis created — {ticker.upper()}",
            description=title,
            color=discord.Color.green(),
        )
        embed.add_field(name="ID", value=f"#{thesis.id}", inline=True)
        embed.add_field(name="Entry", value=f"{entry_price:,.0f} VND", inline=True)
        embed.add_field(name="Target", value=f"{target_price:,.0f} VND", inline=True)
        embed.add_field(name="Stop Loss", value=f"{stop_loss:,.0f} VND", inline=True)
        embed.add_field(name="Upside", value=upside_str, inline=True)
        embed.add_field(name="R/R", value=rr_str, inline=True)
        if summary:
            embed.add_field(name="Summary", value=summary[:500], inline=False)
        embed.set_footer(text="Use /review_thesis to run AI review")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /thesis list
    # ------------------------------------------------------------------

    @group.command(name="list", description="Show all your investment theses")
    @app_commands.describe(status="Filter by status (default: active)")
    @app_commands.choices(
        status=[
            app_commands.Choice(name="Active", value="active"),
            app_commands.Choice(name="Paused", value="paused"),
            app_commands.Choice(name="Closed", value="closed"),
            app_commands.Choice(name="Invalidated", value="invalidated"),
            app_commands.Choice(name="All", value="all"),
        ]
    )
    async def thesis_list(
        self,
        interaction: discord.Interaction,
        status: str = "active",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                svc = ThesisService(session)
                filter_status = None if status == "all" else ThesisStatus(status)
                theses = await svc.list_for_user(user_id=user_id, status=filter_status)
        except Exception as exc:
            logger.error("thesis_list.error", error=str(exc))
            await self.send_error(interaction, title="Error", description=str(exc))
            return

        if not theses:
            await self.send_ok(
                interaction,
                title="Your theses",
                description=(
                    f"No **{status}** theses found.\nUse `/thesis add` to create your first thesis."
                ),
            )
            return

        lines = []
        for t in theses[:20]:
            icon = STATUS_ICON.get(t.status, "⚪")
            upside = f" · +{t.upside_pct:.0f}%" if t.upside_pct is not None else ""
            score = f" · Score {t.score:.0f}" if t.score is not None else ""
            lines.append(f"{icon} **#{t.id} {t.ticker}** — {t.title[:40]}{upside}{score}")

        embed = discord.Embed(
            title=f"📋 Your Theses ({status})",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"{len(theses)} thesis(es) · /review_thesis <id> to run AI review")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /thesis close
    # ------------------------------------------------------------------

    @group.command(name="close", description="Close or invalidate a thesis")
    @app_commands.describe(
        thesis_id="Thesis ID to close (from /thesis list)",
        reason="closed or invalidated",
    )
    @app_commands.choices(
        reason=[
            app_commands.Choice(name="Closed (target reached / exit)", value="closed"),
            app_commands.Choice(name="Invalidated (thesis broken)", value="invalidated"),
        ]
    )
    async def thesis_close(
        self,
        interaction: discord.Interaction,
        thesis_id: int,
        reason: str = "closed",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                svc = ThesisService(session)
                if reason == "invalidated":
                    await svc.invalidate(thesis_id=thesis_id, user_id=user_id)
                else:
                    await svc.close(thesis_id=thesis_id, user_id=user_id)
        except ThesisNotFoundError:
            await self.send_error(
                interaction,
                title="Not found",
                description=f"Thesis **#{thesis_id}** not found or doesn't belong to you.",
            )
            return
        except Exception as exc:
            logger.error("thesis_close.error", thesis_id=thesis_id, error=str(exc))
            await self.send_error(interaction, title="Error", description=str(exc))
            return

        icon = "✅" if reason == "closed" else "❌"
        await self.send_ok(
            interaction,
            title=f"{icon} Thesis #{thesis_id} {reason}",
            description=f"Status updated to **{reason}**.",
        )
