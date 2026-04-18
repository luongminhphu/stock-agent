"""Briefing commands cog.

Owner: bot segment.
Commands:
    /morning_brief  — generate and display morning market brief
    /eod_brief      — generate and display end-of-day brief

No business logic — call BriefingService, format embed, done.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands.base import BaseCog
from src.briefing.service import BriefingService
from src.ai.schemas import MarketSentiment
from src.platform.bootstrap import get_briefing_agent, get_quote_service
from src.platform.db import AsyncSessionLocal
from src.platform.logging import get_logger
from src.watchlist.service import WatchlistService

logger = get_logger(__name__)

_SENTIMENT_ICON: dict[MarketSentiment, str] = {
    MarketSentiment.RISK_ON:    "🟢",
    MarketSentiment.RISK_OFF:   "🔴",
    MarketSentiment.MIXED:      "🟡",
    MarketSentiment.UNCERTAIN:  "⚪",
}

_SENTIMENT_COLOUR: dict[MarketSentiment, discord.Color] = {
    MarketSentiment.RISK_ON:    discord.Color.green(),
    MarketSentiment.RISK_OFF:   discord.Color.red(),
    MarketSentiment.MIXED:      discord.Color.yellow(),
    MarketSentiment.UNCERTAIN:  discord.Color.greyple(),
}


class BriefingCog(BaseCog):
    """Slash commands: /morning_brief, /eod_brief"""

    @app_commands.command(
        name="morning_brief",
        description="Nhận morning brief thị trường cho watchlist của bạn",
    )
    async def morning_brief(self, interaction: discord.Interaction) -> None:
        await self._run_brief(interaction, phase="morning")

    @app_commands.command(
        name="eod_brief",
        description="Nhận end-of-day brief tóm tắt phiên giao dịch",
    )
    async def eod_brief(self, interaction: discord.Interaction) -> None:
        await self._run_brief(interaction, phase="eod")

    async def _run_brief(self, interaction: discord.Interaction, phase: str) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = str(interaction.user.id)

        try:
            async with AsyncSessionLocal() as session:
                svc = BriefingService(
                    watchlist_service=WatchlistService(session=session),
                    quote_service=get_quote_service(),
                    briefing_agent=get_briefing_agent(),
                )
                if phase == "morning":
                    brief = await svc.generate_morning_brief(user_id=user_id)
                else:
                    brief = await svc.generate_eod_brief(user_id=user_id)
                await session.commit()
        except Exception as exc:
            logger.error(f"bot.{phase}_brief.error", user_id=user_id, error=str(exc))
            await self.send_error(
                interaction,
                title="Brief generation failed",
                description=f"Không thể tạo {phase} brief.\nError: `{exc}`",
            )
            return

        embed = _build_brief_embed(brief, phase=phase)
        await interaction.followup.send(embed=embed, ephemeral=True)


def _build_brief_embed(brief: object, phase: str) -> discord.Embed:
    from src.ai.schemas import BriefOutput
    assert isinstance(brief, BriefOutput)

    try:
        sentiment = MarketSentiment(brief.sentiment)
    except ValueError:
        sentiment = MarketSentiment.UNCERTAIN

    icon   = _SENTIMENT_ICON.get(sentiment, "⚪")
    colour = _SENTIMENT_COLOUR.get(sentiment, discord.Color.greyple())
    phase_label = "Morning Brief" if phase == "morning" else "EOD Brief"

    embed = discord.Embed(
        title=f"{icon} {phase_label} — {brief.headline}",
        description=brief.summary,
        colour=colour,
    )

    if brief.key_movers:
        embed.add_field(
            name="📊 Key Movers",
            value="\n".join(f"• {m}" for m in brief.key_movers[:6]),
            inline=False,
        )

    if brief.watchlist_alerts:
        embed.add_field(
            name="👁️ Watchlist Alerts",
            value="\n".join(f"• {a}" for a in brief.watchlist_alerts[:5]),
            inline=False,
        )

    if brief.action_items:
        embed.add_field(
            name="✅ Action Items",
            value="\n".join(f"• {a}" for a in brief.action_items[:5]),
            inline=False,
        )

    embed.set_footer(text=f"Sentiment: {sentiment.value} • stock-agent AI")
    return embed
