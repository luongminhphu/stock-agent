"""Why command — /why <ticker>
Owner: bot segment. Adapter only — no domain logic.
"""
from __future__ import annotations
import discord
from discord import app_commands
from src.ai.schemas import MovementDirection
from src.bot.commands.base import BaseCog
from src.market.why_service import WhyService
from src.platform.bootstrap import get_ohlcv_service, get_quote_service, get_why_agent
from src.platform.logging import get_logger

logger = get_logger(__name__)

_DIR_EMOJI = {
    MovementDirection.UP:   "📈",
    MovementDirection.DOWN: "📉",
    MovementDirection.FLAT: "➡️",
}
_DIR_COLOR = {
    MovementDirection.UP:   discord.Color.green(),
    MovementDirection.DOWN: discord.Color.red(),
    MovementDirection.FLAT: discord.Color.greyple(),
}


class WhyCog(BaseCog):
    @app_commands.command(name="why", description="Phân tích nguyên nhân tăng/giảm của mã CK")
    @app_commands.describe(ticker="Mã cổ phiếu, VD: HPG")
    async def why(self, interaction: discord.Interaction, ticker: str) -> None:
        await interaction.response.defer(ephemeral=False)
        try:
            svc = WhyService(
                quote_service=get_quote_service(),
                ohlcv_service=get_ohlcv_service(),
                why_agent=get_why_agent(),
            )
            result = await svc.explain(ticker)
        except Exception as exc:
            logger.error("why.command.error", ticker=ticker, error=str(exc))
            await self.send_error(
                interaction,
                title="Phân tích thất bại",
                description=f"Không thể phân tích `{ticker.upper()}`.\n`{exc}`",
            )
            return

        embed = _build_why_embed(result)
        await interaction.followup.send(embed=embed, ephemeral=False)


def _build_why_embed(result) -> discord.Embed:
    emoji = _DIR_EMOJI.get(result.direction, "❓")
    color = _DIR_COLOR.get(result.direction, discord.Color.blurple())
    sign = "+" if result.change_pct > 0 else ""

    embed = discord.Embed(
        title=f"{emoji} Tại sao {result.ticker} {sign}{result.change_pct:.2f}%?",
        description=f"**{result.headline}**",
        color=color,
    )
    if result.causes:
        embed.add_field(
            name="🔍 Nguyên nhân",
            value="\n".join(f"• {c}" for c in result.causes),
            inline=False,
        )
    if result.macro_context:
        embed.add_field(name="🌐 Vĩ mô", value=result.macro_context, inline=False)
    if result.risk_flags:
        embed.add_field(
            name="⚠️ Rủi ro cần theo dõi",
            value="\n".join(f"• {r}" for r in result.risk_flags),
            inline=False,
        )
    conf_bar = "█" * round(result.confidence * 10) + "░" * (10 - round(result.confidence * 10))
    embed.set_footer(text=f"Độ tin cậy: {conf_bar} {result.confidence:.0%}  ·  stock-agent AI")
    if result.data_quality:
        embed.add_field(name="📊 Ghi chú dữ liệu", value=result.data_quality, inline=False)
    return embed
