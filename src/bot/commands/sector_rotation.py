"""Discord Cog cho /sector command.

Owner: bot segment. Thin adapter — không chứa domain logic.
Mọi xử lý nằm trong SectorRotationAgent (ai segment).

Usage:
    /sector             — phân tích toàn thị trường
    /sector VCB VNM BID — scope theo watchlist (space-separated tickers)
"""

from __future__ import annotations

import discord
from discord import app_commands

from src.ai.schemas import FlowDirection
from src.bot.commands.base import BaseCog
from src.platform.bootstrap import get_sector_rotation_agent
from src.platform.logging import get_logger

logger = get_logger(__name__)


class SectorRotationCog(BaseCog):
    @app_commands.command(
        name="sector",
        description="Phân tích dòng tiền theo sector hôm nay",
    )
    @app_commands.describe(
        tickers="(Tuỳ chọn) Danh sách mã cách nhau bằng dấu cách, VD: VCB VNM BID"
    )
    async def sector(self, interaction: discord.Interaction, tickers: str | None = None) -> None:
        await interaction.response.defer(ephemeral=False)

        watchlist: list[str] | None = None
        if tickers:
            watchlist = [t.strip().upper() for t in tickers.split() if t.strip()]

        try:
            agent = get_sector_rotation_agent()
            result = await agent.analyze(watchlist_tickers=watchlist)  # type: ignore[union-attr]
        except Exception as exc:
            logger.error("sector.command.error", error=str(exc))
            await self.send_error(
                interaction,
                title="Phân tích sector thất bại",
                description=f"Không thể phân tích dòng tiền lúc này.\n`{exc}`",
            )
            return

        embed = _build_sector_embed(result)
        await interaction.followup.send(embed=embed, ephemeral=False)


def _build_sector_embed(result) -> discord.Embed:
    regime_emoji = {"RISK_ON": "🟢", "RISK_OFF": "🔴", "MIXED": "🟡"}.get(
        str(result.risk_regime), ""
    )
    color = {
        "RISK_ON": discord.Color.green(),
        "RISK_OFF": discord.Color.red(),
        "MIXED": discord.Color.gold(),
    }.get(str(result.risk_regime), discord.Color.blurple())

    embed = discord.Embed(
        title=f"{regime_emoji} Sector Rotation Radar — {result.snapshot_date}",
        description=result.rotation_narrative,
        color=color,
    )

    if result.leading_sectors:
        lines = []
        for sf in result.leading_sectors[:3]:
            movers = ", ".join(sf.top_movers[:3]) if sf.top_movers else "—"
            lines.append(f"**{sf.sector}** `{sf.avg_change_pct_1d:+.2f}%` — {movers}")
        embed.add_field(name="⬆️ Leading Sectors", value="\n".join(lines), inline=False)

    if result.lagging_sectors:
        lines = []
        for sf in result.lagging_sectors[:3]:
            movers = ", ".join(sf.top_movers[:3]) if sf.top_movers else "—"
            lines.append(f"**{sf.sector}** `{sf.avg_change_pct_1d:+.2f}%` — {movers}")
        embed.add_field(name="⬇️ Lagging Sectors", value="\n".join(lines), inline=False)

    if result.watchlist_crosscheck:
        lines = []
        for wc in result.watchlist_crosscheck:
            flag = "🚩" if wc.is_contrarian else "✅"
            lines.append(f"{flag} {wc.note}")
        embed.add_field(name="🔍 Watchlist vs Sector", value="\n".join(lines), inline=False)

    if result.actionable_insight:
        embed.add_field(name="💡 Insight", value=result.actionable_insight, inline=False)

    conf_bar = "█" * round(result.confidence * 10) + "░" * (10 - round(result.confidence * 10))
    embed.set_footer(text=f"Confidence: {conf_bar} {result.confidence:.0%}  ·  stock-agent AI")
    return embed
