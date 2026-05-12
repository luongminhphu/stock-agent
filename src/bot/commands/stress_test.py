"""Stress-Test command cog.

Owner: bot segment.
Adapter only: Discord interaction → StressTestService → format embed.
NO business logic here.
"""

from __future__ import annotations

import discord
from discord import app_commands

from src.ai.schemas import StressTestOutput, ThreatLevel
from src.bot.commands.base import BaseCog
from src.platform.bootstrap import get_quote_service, get_stress_test_agent
from src.platform.logging import get_logger
from src.thesis.stress_test_service import StressTestService

logger = get_logger(__name__)

_THREAT_COLOUR = {
    ThreatLevel.LOW:      discord.Color.green(),
    ThreatLevel.MEDIUM:   discord.Color.gold(),
    ThreatLevel.HIGH:     discord.Color.orange(),
    ThreatLevel.CRITICAL: discord.Color.red(),
}

_THREAT_EMOJI = {
    ThreatLevel.LOW:      "὾2",
    ThreatLevel.MEDIUM:   "὾1",
    ThreatLevel.HIGH:     "ὓ4",
    ThreatLevel.CRITICAL: "💣",
}


class StressTestCog(BaseCog):
    """Slash command: /stress_test <ticker>"""

    @app_commands.command(
        name="stress_test",
        description="Stress-test thesis assumptions cho một mã cổ phiếu",
    )
    @app_commands.describe(ticker="Mã cổ phiếu (VD: VCB, HPG, VNM)")
    async def stress_test(self, interaction: discord.Interaction, ticker: str) -> None:
        await interaction.response.defer(ephemeral=False)
        user_id = self.user_id(interaction)
        ticker = ticker.upper().strip()

        try:
            async with self.db_session() as session:
                svc = StressTestService(
                    session=session,
                    agent=get_stress_test_agent(),
                    quote_service=get_quote_service(),
                )
                result = await svc.stress_test_by_ticker(
                    ticker=ticker,
                    user_id=user_id,
                )
        except ValueError as exc:
            await self.send_error(
                interaction,
                title=f"Stress-test {ticker} thất bại",
                description=str(exc),
            )
            return
        except Exception as exc:
            logger.error("stress_test.command.error", ticker=ticker, error=str(exc))
            await self.send_error(
                interaction,
                title="Lỗi hệ thống",
                description=f"Không thể stress-test {ticker}.\n`{exc}`",
            )
            return

        embed = build_stress_test_embed(result)
        await interaction.followup.send(embed=embed, ephemeral=False)


def build_stress_test_embed(result: StressTestOutput) -> discord.Embed:
    """Convert StressTestOutput → Discord Embed.

    Public — importable by other bot adapters if needed.
    Maps directly to StressTestOutput fields in ai.schemas.
    """
    colour = _THREAT_COLOUR.get(result.overall_threat, discord.Color.greyple())
    threat_emoji = _THREAT_EMOJI.get(result.overall_threat, "⚪")

    embed = discord.Embed(
        title=f"🔬 Stress-Test: {result.ticker}",
        description=f"**Scenario:** _{result.scenario}_",
        color=colour,
    )

    # Overall threat + confidence
    embed.add_field(
        name="Overall Threat",
        value=f"{threat_emoji} **{result.overall_threat}** (conf: {result.confidence:.0%})",
        inline=True,
    )

    # Threatened assumptions
    if result.threatened_assumptions:
        lines = []
        for a in result.threatened_assumptions:
            emoji = _THREAT_EMOJI.get(a.threat_level, "⚪")
            lines.append(f"{emoji} **{a.threat_level}** — {a.assumption_text[:80]}")
            lines.append(f"  ↳ _{a.evidence[:120]}_")
            if a.probability_of_invalidation > 0:
                lines.append(
                    f"  📊 Xác suất invalidation: **{a.probability_of_invalidation:.0%}**"
                )
        embed.add_field(
            name=f"⚠️ Assumptions bị đe dọa ({len(result.threatened_assumptions)})",
            value="\n".join(lines)[:1024],
            inline=False,
        )

    # Hedge suggestions
    if result.hedge_suggestions:
        hedges_text = "\n".join(f"🛡️ {h[:100]}" for h in result.hedge_suggestions[:4])
        embed.add_field(
            name="Gợi ý hedge",
            value=hedges_text[:1024],
            inline=False,
        )

    # Portfolio impact
    if result.portfolio_impact_note:
        embed.add_field(
            name="Portfolio Impact",
            value=result.portfolio_impact_note[:512],
            inline=False,
        )

    # Summary
    if result.summary:
        embed.add_field(
            name="Tóm tắt",
            value=result.summary[:512],
            inline=False,
        )

    embed.set_footer(text="stock-agent · Stress-Test AI — read-only, không thay đổi thesis")
    return embed


def _prob_bar(prob: float, length: int = 8) -> str:
    """Render a simple ASCII probability bar.

    Example: 0.6 → '█████░░░'
    """
    filled = round(prob * length)
    return "█" * filled + "░" * (length - filled)
