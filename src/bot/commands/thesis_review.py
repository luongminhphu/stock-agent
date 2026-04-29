"""Thesis review & recommendation commands cog.

Owner: bot segment.
Commands:
    /review_thesis   — trigger AI review, show result in embed
    /recommendations — list pending AI recommendations for a thesis
    /accept          — accept a recommendation (apply to assumption/catalyst)
    /reject          — reject a recommendation (dismiss without applying)

Adapter only: parse input → call ReviewService / ThesisService → format embed.
No business logic.
"""

from __future__ import annotations

import discord
from discord import app_commands

from src.bot.commands.base import BaseCog
from src.bot.commands.thesis_embeds import TARGET_ICON, build_review_embed
from src.platform.bootstrap import get_quote_service, get_thesis_review_agent
from src.platform.logging import get_logger
from src.thesis.models import ThesisStatus
from src.thesis.review_service import ReviewNotAllowedError, ReviewService
from src.thesis.service import ThesisNotFoundError, ThesisService

logger = get_logger(__name__)


class ThesisReviewCog(BaseCog):
    """Slash commands: /review_thesis, /recommendations, /accept, /reject."""

    # ------------------------------------------------------------------
    # /review_thesis
    # ------------------------------------------------------------------

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

        embed = build_review_embed(review)
        embed.set_footer(
            text=f"{embed.footer.text} · /recommendations {thesis_id} to review AI suggestions"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /recommendations
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
            icon = TARGET_ICON.get(rec.target_type, "•")
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

    # ------------------------------------------------------------------
    # /accept & /reject — shared handler
    # ------------------------------------------------------------------

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
