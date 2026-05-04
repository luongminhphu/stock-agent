"""Stress-Test command cog.

Owner: bot segment.
Adapter only: Discord interaction → StressTestService → format embed.

NO business logic here.
"""

from __future__ import annotations

import discord
from discord import app_commands

from src.ai.schemas import StressTestOutput, ThreatLevel, Verdict
from src.bot.commands.base import BaseCog
from src.platform.bootstrap import get_quote_service, get_stress_test_agent
from src.platform.logging import get_logger
from src.thesis.stress_test_service import StressTestService

logger = get_logger(__name__)

_VERDICT_COLOUR = {
    Verdict.BULLISH:   discord.Color.green(),
    Verdict.NEUTRAL:   discord.Color.gold(),
    Verdict.BEARISH:   discord.Color.red(),
    Verdict.WATCHLIST: discord.Color.blurple(),
}

_THREAT_EMOJI = {
    ThreatLevel.INTACT:   "🟢",
    ThreatLevel.WEAKENED: "🟡",
    ThreatLevel.BROKEN:   "🔴",
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
    """
    colour = _VERDICT_COLOUR.get(result.verdict, discord.Color.greyple())
    prob_pct = result.invalidation_probability * 100
    prob_bar = _prob_bar(result.invalidation_probability)

    embed = discord.Embed(
        title=f"🔬 Stress-Test: {result.ticker}",
        description=(
            f"**{result.thesis_title}**\n"
            f"Scenario: _{result.stress_scenario}_"
        ),
        color=colour,
    )

    # Verdict + probability
    verdict_emoji = {Verdict.BULLISH: "🟢", Verdict.NEUTRAL: "🟡", Verdict.BEARISH: "🔴"}.get(
        result.verdict, "⚪"
    )
    embed.add_field(
        name="Verdict",
        value=f"{verdict_emoji} **{result.verdict}** (conf: {result.confidence:.0%})",
        inline=True,
    )
    embed.add_field(
        name="Xác suất invalidation",
        value=f"{prob_bar} **{prob_pct:.0f}%**",
        inline=True,
    )

    # Threatened assumptions
    if result.threatened_assumptions:
        lines = []
        for a in result.threatened_assumptions:
            emoji = _THREAT_EMOJI.get(a.threat_level, "⚪")
            lines.append(f"{emoji} **{a.threat_level}** — {a.description[:80]}")
            lines.append(f"  ↳ _{a.counter_argument[:120]}_")
        embed.add_field(
            name=f"⚠️ Assumptions bị đe dọa ({len(result.threatened_assumptions)})",
            value="\n".join(lines)[:1024],
            inline=False,
        )

    # Surviving assumptions
    if result.surviving_assumptions:
        surviving_text = "\n".join(f"✅ {s[:100]}" for s in result.surviving_assumptions[:4])
        embed.add_field(
            name=f"💪 Assumptions còn vững ({len(result.surviving_assumptions)})",
            value=surviving_text[:1024],
            inline=False,
        )

    # Triggers to watch
    if result.suggested_triggers_to_watch:
        triggers_text = "\n".join(
            f"👁️ {t[:100]}" for t in result.suggested_triggers_to_watch[:4]
        )
        embed.add_field(
            name="Triggers cần theo dõi",
            value=triggers_text[:1024],
            inline=False,
        )

    # Macro risks
    if result.macro_risks:
        risks_text = "\n".join(f"• {r[:100]}" for r in result.macro_risks[:3])
        embed.add_field(name="Rủi ro vĩ mô", value=risks_text[:512], inline=False)

    # Reasoning
    if result.reasoning:
        embed.add_field(
            name="Lý giải",
            value=result.reasoning[:512],
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
