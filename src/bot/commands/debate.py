"""Thesis debate command cog.

Owner: bot segment.
Command:
    /debate — run ThesisDebateAgent on a thesis, display challenges + verdict.

Adapter only: parse input → call ThesisDebateAgent → format embed.
No business logic.
"""

from __future__ import annotations

import discord
from discord import app_commands

from src.bot.commands.base import BaseCog
from src.bot.commands.debate_embeds import build_debate_embed
from src.platform.bootstrap import get_quote_service, get_thesis_debate_agent
from src.platform.logging import get_logger
from src.thesis.service import ThesisNotFoundError, ThesisService

logger = get_logger(__name__)


class DebateCog(BaseCog):
    """Slash command: /debate."""

    @app_commands.command(
        name="debate",
        description="Challenge your investment thesis with an AI devil's advocate",
    )
    @app_commands.describe(thesis_id="Numeric thesis ID (from /thesis list)")
    async def debate(
        self,
        interaction: discord.Interaction,
        thesis_id: int,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                thesis_svc = ThesisService(session)
                thesis = await thesis_svc.get(thesis_id=thesis_id, user_id=user_id)

            agent = get_thesis_debate_agent()
            quote_svc = get_quote_service()

            # Fetch current price for market context (best-effort)
            current_price: float | None = None
            try:
                quote = await quote_svc.get_quote(thesis.ticker)
                current_price = quote.price if quote else None
            except Exception:
                pass

            result = await agent.run(
                thesis=thesis,
                current_price=current_price,
            )

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
            logger.error(
                "bot.debate.error",
                thesis_id=thesis_id,
                error=str(exc),
            )
            await self.send_error(
                interaction,
                title="Debate failed",
                description=(
                    f"Could not complete debate for thesis **#{thesis_id}**.\n"
                    f"Error: `{exc}`"
                ),
            )
            return

        embed = build_debate_embed(thesis_id=thesis_id, ticker=thesis.ticker, result=result)
        await interaction.followup.send(embed=embed, ephemeral=True)
