"""Thesis commands cog.

Owner: bot segment.
Commands:
    /review_thesis <thesis_id>  — trigger AI review, show result in embed

No business logic — parse input → call ReviewService → format embed.
"""
from __future__ import annotations

import json

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands.base import BaseCog
from src.platform.bootstrap import get_quote_service, get_thesis_review_agent
from src.platform.db import AsyncSessionLocal
from src.platform.logging import get_logger
from src.thesis.models import ReviewVerdict
from src.thesis.review_service import ReviewNotAllowedError, ReviewService
from src.thesis.service import ThesisNotFoundError

logger = get_logger(__name__)

# Colour map by verdict
_VERDICT_COLOUR = {
    ReviewVerdict.BULLISH:   discord.Color.green(),
    ReviewVerdict.BEARISH:   discord.Color.red(),
    ReviewVerdict.NEUTRAL:   discord.Color.yellow(),
    ReviewVerdict.WATCHLIST: discord.Color.blue(),
}
_VERDICT_ICON = {
    ReviewVerdict.BULLISH:   "🟢",
    ReviewVerdict.BEARISH:   "🔴",
    ReviewVerdict.NEUTRAL:   "🟡",
    ReviewVerdict.WATCHLIST: "🔵",
}


class ThesisCog(BaseCog):
    """Slash commands: /review_thesis"""

    @app_commands.command(
        name="review_thesis",
        description="Run an AI review on one of your investment theses",
    )
    @app_commands.describe(thesis_id="Numeric thesis ID (from /my_theses)")
    async def review_thesis(
        self,
        interaction: discord.Interaction,
        thesis_id: int,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = str(interaction.user.id)

        try:
            async with AsyncSessionLocal() as session:
                svc = ReviewService(
                    session=session,
                    agent=get_thesis_review_agent(),
                    quote_service=get_quote_service(),
                )
                review = await svc.review_thesis(
                    thesis_id=thesis_id,
                    user_id=user_id,
                )
                await session.commit()
        except ThesisNotFoundError:
            await self.send_error(
                interaction,
                title="Thesis not found",
                description=(
                    f"Thesis **#{thesis_id}** not found or doesn't belong to you.\n"
                    "Use `/my_theses` to see your thesis list."
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
        await interaction.followup.send(embed=embed, ephemeral=True)


def _build_review_embed(review: object) -> discord.Embed:
    """Build Discord embed from a ThesisReview ORM instance."""
    verdict = ReviewVerdict(review.verdict)  # type: ignore[attr-defined]
    colour = _VERDICT_COLOUR.get(verdict, discord.Color.greyple())
    icon   = _VERDICT_ICON.get(verdict, "⚪")

    confidence_bar = _confidence_bar(review.confidence)  # type: ignore[attr-defined]

    embed = discord.Embed(
        title=f"{icon} Thesis #{review.thesis_id} — {verdict.value}",  # type: ignore[attr-defined]
        description=review.reasoning[:1000] if review.reasoning else "",  # type: ignore[attr-defined]
        colour=colour,
    )

    embed.add_field(
        name="Confidence",
        value=f"{confidence_bar} `{review.confidence:.0%}`",  # type: ignore[attr-defined]
        inline=False,
    )

    # Risk signals
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

    # Next watch items
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

    price_str = (
        f"{review.reviewed_price:,.0f} VND"  # type: ignore[attr-defined]
        if review.reviewed_price
        else "N/A"
    )
    reviewed_at = getattr(review, "reviewed_at", None)
    ts_str = reviewed_at.strftime("%H:%M %d/%m/%Y") if reviewed_at else "N/A"

    embed.set_footer(text=f"Price at review: {price_str} • {ts_str} • stock-agent AI")
    return embed


def _confidence_bar(confidence: float, length: int = 10) -> str:
    """Visual confidence bar: ██████░░░░ 60%"""
    filled = round(confidence * length)
    return "█" * filled + "░" * (length - filled)
