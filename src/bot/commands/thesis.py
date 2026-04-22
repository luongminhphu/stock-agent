"""Thesis commands cog.

Owner: bot segment.
Commands:
    /thesis add        — create a new investment thesis
    /thesis list       — show all your theses
    /thesis close      — close/invalidate a thesis
    /review_thesis     — trigger AI review, show result in embed
    /recommendations   — list pending AI recommendations for a thesis
    /accept            — accept a recommendation (apply to assumption/catalyst)
    /reject            — reject a recommendation (dismiss without applying)

No business logic — parse input → call domain service → format embed.
"""

from __future__ import annotations

import json

import discord
from discord import app_commands

from src.bot.commands.base import BaseCog
from src.platform.bootstrap import get_quote_service, get_thesis_review_agent
from src.platform.logging import get_logger
from src.thesis.models import ReviewVerdict, ThesisStatus
from src.thesis.review_service import ReviewNotAllowedError, ReviewService
from src.thesis.service import CreateThesisInput, ThesisNotFoundError, ThesisService

logger = get_logger(__name__)

_VERDICT_COLOUR = {
    ReviewVerdict.BULLISH: discord.Color.green(),
    ReviewVerdict.BEARISH: discord.Color.red(),
    ReviewVerdict.NEUTRAL: discord.Color.yellow(),
    ReviewVerdict.WATCHLIST: discord.Color.blue(),
}
_VERDICT_ICON = {
    ReviewVerdict.BULLISH: "🟢",
    ReviewVerdict.BEARISH: "🔴",
    ReviewVerdict.NEUTRAL: "🟡",
    ReviewVerdict.WATCHLIST: "🔵",
}
_STATUS_ICON = {
    ThesisStatus.ACTIVE: "🟢",
    ThesisStatus.PAUSED: "⏸️",
    ThesisStatus.INVALIDATED: "❌",
    ThesisStatus.CLOSED: "✅",
}
_TARGET_ICON = {
    "assumption": "📌",
    "catalyst": "⚡",
}


class ThesisCog(BaseCog):
    """Slash commands: /thesis group + /review_thesis + /recommendations + /accept + /reject."""

    group = app_commands.Group(
        name="thesis",
        description="Manage your investment theses",
    )

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
            icon = _STATUS_ICON.get(t.status, "⚪")
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

    @app_commands.command(
        name="review_thesis",
        description="Run an AI review on one of your investment theses",
    )
    @app_commands.describe(thesis_id="Numeric thesis ID (from /thesis list)")
    async def review_thesis(
        self,
        interaction: discord.Interaction,
        thesis_id: int,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                svc = ReviewService(
                    session=session,
                    agent=get_thesis_review_agent(),
                    quote_service=get_quote_service(),
                )
                review = await svc.review_thesis(thesis_id=thesis_id, user_id=user_id)
        except ThesisNotFoundError:
            await self.send_error(
                interaction,
                title="Thesis not found",
                description=(
                    f"Thesis **#{thesis_id}** not found or doesn't belong to you.\n"
                    "Use `/thesis list` to see your thesis list."
                ),
            )
            return
        except ReviewNotAllowedError as exc:
            await self.send_error(
                interaction,
                title="Review not allowed",
                description=str(exc),
            )
            return
        except Exception as exc:
            logger.error("bot.review_thesis.error", thesis_id=thesis_id, error=str(exc))
            await self.send_error(
                interaction,
                title="AI review failed",
                description=f"Could not complete review for thesis **#{thesis_id}**.\nError: `{exc}`",
            )
            return

        embed = _build_review_embed(review)
        embed.set_footer(
            text=f"{embed.footer.text} · /recommendations {thesis_id} to review AI suggestions"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # Wave 4a — Recommendation commands
    # ------------------------------------------------------------------

    @app_commands.command(
        name="recommendations",
        description="List pending AI recommendations for a thesis",
    )
    @app_commands.describe(thesis_id="Numeric thesis ID (from /thesis list)")
    async def recommendations(
        self,
        interaction: discord.Interaction,
        thesis_id: int,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                svc = ReviewService(
                    session=session,
                    agent=get_thesis_review_agent(),
                    quote_service=get_quote_service(),
                )
                recs = await svc.list_pending_recommendations(thesis_id=thesis_id, user_id=user_id)
        except ThesisNotFoundError:
            await self.send_error(
                interaction,
                title="Thesis not found",
                description=(
                    f"Thesis **#{thesis_id}** not found or doesn't belong to you.\n"
                    "Use `/thesis list` to see your thesis list."
                ),
            )
            return
        except Exception as exc:
            logger.error("bot.recommendations.error", thesis_id=thesis_id, error=str(exc))
            await self.send_error(interaction, title="Error", description=str(exc))
            return

        if not recs:
            await self.send_ok(
                interaction,
                title=f"📭 No pending recommendations — Thesis #{thesis_id}",
                description=(
                    "All AI recommendations have been acted on, or no review has been run yet.\n"
                    "Use `/review_thesis` to generate new recommendations."
                ),
            )
            return

        embed = discord.Embed(
            title=f"🤖 Pending Recommendations — Thesis #{thesis_id}",
            description=f"**{len(recs)}** recommendation(s) waiting for your decision:",
            color=discord.Color.orange(),
        )
        for rec in recs[:10]:
            icon = _TARGET_ICON.get(rec.target_type, "•")
            field_name = (
                f"{icon} #{rec.id} · {rec.target_type.capitalize()} → `{rec.recommended_status}`"
            )
            field_value = (
                f"**{rec.target_description[:80]}**\n"
                f"_{rec.reason[:120] if rec.reason else 'No reason provided'}_"
            )
            embed.add_field(name=field_name, value=field_value, inline=False)

        if len(recs) > 10:
            embed.add_field(
                name="…",
                value=f"and {len(recs) - 10} more. Act on the above first.",
                inline=False,
            )
        embed.set_footer(text="Use /accept <id> or /reject <id> to act on each recommendation")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="accept",
        description="Accept an AI recommendation and apply it to the thesis",
    )
    @app_commands.describe(
        thesis_id="Thesis ID the recommendation belongs to",
        recommendation_id="Recommendation ID (from /recommendations)",
    )
    async def accept(
        self,
        interaction: discord.Interaction,
        thesis_id: int,
        recommendation_id: int,
    ) -> None:
        await self._apply_recommendation(
            interaction, thesis_id=thesis_id, recommendation_id=recommendation_id, accept=True
        )

    @app_commands.command(
        name="reject",
        description="Reject an AI recommendation without applying it",
    )
    @app_commands.describe(
        thesis_id="Thesis ID the recommendation belongs to",
        recommendation_id="Recommendation ID (from /recommendations)",
    )
    async def reject(
        self,
        interaction: discord.Interaction,
        thesis_id: int,
        recommendation_id: int,
    ) -> None:
        await self._apply_recommendation(
            interaction, thesis_id=thesis_id, recommendation_id=recommendation_id, accept=False
        )

    async def _apply_recommendation(
        self,
        interaction: discord.Interaction,
        thesis_id: int,
        recommendation_id: int,
        accept: bool,
    ) -> None:
        """Shared handler for /accept and /reject — keeps both commands thin."""
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)
        action = "accept" if accept else "reject"

        try:
            async with self.db_session() as session:
                # Reload thesis after apply to detect auto-invalidation
                thesis_svc = ThesisService(session)
                await thesis_svc.apply_recommendation(
                    thesis_id=thesis_id,
                    recommendation_id=recommendation_id,
                    user_id=user_id,
                    accept=accept,
                )
                # Reload thesis to check if it was auto-invalidated
                thesis = await thesis_svc.get(thesis_id=thesis_id, user_id=user_id)
        except ThesisNotFoundError:
            await self.send_error(
                interaction,
                title="Thesis not found",
                description=f"Thesis **#{thesis_id}** not found or doesn't belong to you.",
            )
            return
        except ValueError as exc:
            await self.send_error(
                interaction,
                title="Invalid recommendation",
                description=str(exc),
            )
            return
        except Exception as exc:
            logger.error(
                f"bot.{action}_recommendation.error",
                thesis_id=thesis_id,
                recommendation_id=recommendation_id,
                error=str(exc),
            )
            await self.send_error(interaction, title="Error", description=str(exc))
            return

        icon = "✅" if accept else "🚫"
        action_label = "accepted" if accept else "rejected"
        embed = discord.Embed(
            title=f"{icon} Recommendation #{recommendation_id} {action_label}",
            color=discord.Color.green() if accept else discord.Color.greyple(),
        )

        # Warn user if thesis was auto-invalidated as a result of accepting
        if accept and thesis.status == ThesisStatus.INVALIDATED:
            embed.add_field(
                name="⚠️ Thesis Auto-Invalidated",
                value=(
                    f"Thesis **#{thesis_id}** has been automatically invalidated "
                    "because more than 50% of its assumptions are now INVALID."
                ),
                inline=False,
            )
            embed.color = discord.Color.red()

        embed.set_footer(
            text=f"Thesis #{thesis_id} · /recommendations {thesis_id} to see remaining"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


def _build_review_embed(review: object) -> discord.Embed:
    verdict = ReviewVerdict(review.verdict)  # type: ignore[attr-defined]
    colour = _VERDICT_COLOUR.get(verdict, discord.Color.greyple())
    icon = _VERDICT_ICON.get(verdict, "⚪")

    embed = discord.Embed(
        title=f"{icon} Thesis #{review.thesis_id} — {verdict.value}",  # type: ignore[attr-defined]
        description=review.reasoning[:1000] if review.reasoning else "",  # type: ignore[attr-defined]
        colour=colour,
    )
    embed.add_field(
        name="Confidence",
        value=f"{_confidence_bar(review.confidence)} `{review.confidence:.0%}`",  # type: ignore[attr-defined]
        inline=False,
    )

    try:
        risks = json.loads(review.risk_signals or "[]")  # type: ignore[attr-defined]
    except (json.JSONDecodeError, TypeError):
        risks = []
    if risks:
        embed.add_field(
            name="⚠️ Risk Signals",
            value="\n".join(f"• {r}" for r in risks[:5]),
            inline=False,
        )

    try:
        watches = json.loads(review.next_watch_items or "[]")  # type: ignore[attr-defined]
    except (json.JSONDecodeError, TypeError):
        watches = []
    if watches:
        embed.add_field(
            name="👁️ Watch Next",
            value="\n".join(f"• {w}" for w in watches[:5]),
            inline=False,
        )

    price_str = f"{review.reviewed_price:,.0f} VND" if review.reviewed_price else "N/A"  # type: ignore[attr-defined]
    reviewed_at = getattr(review, "reviewed_at", None)
    ts_str = reviewed_at.strftime("%H:%M %d/%m/%Y") if reviewed_at else "N/A"
    embed.set_footer(text=f"Price at review: {price_str} • {ts_str} • stock-agent AI")
    return embed


def _confidence_bar(confidence: float, length: int = 10) -> str:
    filled = round(confidence * length)
    return "█" * filled + "░" * (length - filled)
