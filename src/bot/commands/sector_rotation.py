"""Discord command adapter cho sector rotation radar.

Thin adapter — không chứa domain logic.
Mọi xử lý nằm trong SectorRotationAgent (ai segment).

Usage:
    /sector             — phân tích toàn thị trường
    /sector VCB VNM BID — scope theo watchlist (space-separated tickers)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord

from src.ai.agents.sector_rotation import SectorRotationAgent
from src.ai.schemas import SectorRotationOutput, FlowDirection

logger = logging.getLogger(__name__)


async def handle_sector_command(
    interaction: "discord.Interaction",
    tickers: str | None,
    agent: SectorRotationAgent,
) -> None:
    """Entry point cho /sector slash command.

    Args:
        interaction: Discord interaction object.
        tickers: Optional space-separated ticker list từ user.
        agent: SectorRotationAgent instance (injected).
    """
    await interaction.response.defer(thinking=True)

    watchlist: list[str] | None = None
    if tickers:
        watchlist = [t.strip().upper() for t in tickers.split() if t.strip()]

    try:
        result = await agent.analyze(watchlist_tickers=watchlist)
    except Exception:
        logger.exception("sector command: agent.analyze failed")
        await interaction.followup.send(
            "❌ Không thể phân tích sector lúc này. Vui lòng thử lại sau."
        )
        return

    embed = _build_embed(result)
    await interaction.followup.send(embed=embed)


def _build_embed(result: SectorRotationOutput) -> "discord.Embed":
    """Render SectorRotationOutput thành Discord Embed."""
    import discord

    regime_emoji = {"RISK_ON": "🟢", "RISK_OFF": "🔴", "MIXED": "🟡"}.get(
        result.risk_regime, ""
    )
    color = {
        "RISK_ON": discord.Color.green(),
        "RISK_OFF": discord.Color.red(),
        "MIXED": discord.Color.gold(),
    }.get(result.risk_regime, discord.Color.blurple())

    embed = discord.Embed(
        title=f"{regime_emoji} Sector Rotation Radar — {result.snapshot_date}",
        description=result.rotation_narrative,
        color=color,
    )

    # Leading sectors
    if result.leading_sectors:
        leading_lines = []
        for sf in result.leading_sectors[:3]:
            movers = ", ".join(sf.top_movers[:3]) if sf.top_movers else "—"
            leading_lines.append(f"**{sf.sector}** `{sf.avg_change_pct_1d:+.2f}%` — {movers}")
        embed.add_field(
            name="⬆️ Leading Sectors",
            value="\n".join(leading_lines),
            inline=False,
        )

    # Lagging sectors
    if result.lagging_sectors:
        lagging_lines = []
        for sf in result.lagging_sectors[:3]:
            movers = ", ".join(sf.top_movers[:3]) if sf.top_movers else "—"
            lagging_lines.append(f"**{sf.sector}** `{sf.avg_change_pct_1d:+.2f}%` — {movers}")
        embed.add_field(
            name="⬇️ Lagging Sectors",
            value="\n".join(lagging_lines),
            inline=False,
        )

    # Watchlist crosscheck
    if result.watchlist_crosscheck:
        cross_lines = []
        for wc in result.watchlist_crosscheck:
            flag = "🚩" if wc.is_contrarian else "✅"
            cross_lines.append(f"{flag} {wc.note}")
        embed.add_field(
            name="🔍 Watchlist vs Sector",
            value="\n".join(cross_lines),
            inline=False,
        )

    # Actionable insight
    if result.actionable_insight:
        embed.add_field(
            name="💡 Insight",
            value=result.actionable_insight,
            inline=False,
        )

    conf_pct = int(result.confidence * 100)
    embed.set_footer(text=f"Confidence: {conf_pct}% | stock-agent")
    return embed
