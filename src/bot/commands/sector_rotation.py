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

from src.ai.agents.sector_rotation import SectorRotationOutput
from src.bot.commands.base import BaseCog
from src.platform.bootstrap import get_sector_rotation_agent, get_quote_service
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
            from src.market.registry import SymbolRegistry
            from src.market.sector_rotation_service import SectorRotationService

            svc = SectorRotationService(
                quote_service=get_quote_service(),
                registry=SymbolRegistry(),
            )
            flows = await svc.get_sector_flows(watchlist_tickers=watchlist)
            snapshot_date = await svc.get_snapshot_date()

            # Convert SectorFlow → list[dict] cho agent
            sector_performance = [
                {
                    "sector": f.sector,
                    "avg_change_pct_1d": f.avg_change_pct_1d,
                    "flow_direction": str(f.flow_direction),
                    "top_movers": f.top_movers,
                    "ticker_count": f.ticker_count,
                }
                for f in flows
            ]

            # Tổng hợp macro_context đơn giản từ data có sẵn
            inflow = [f.sector for f in flows if f.avg_change_pct_1d > 0]
            outflow = [f.sector for f in flows if f.avg_change_pct_1d < 0]
            macro_context = (
                f"Ngày {snapshot_date}. "
                f"Sectors tăng: {', '.join(inflow) or 'không có'}. "
                f"Sectors giảm: {', '.join(outflow) or 'không có'}."
            )

            agent = get_sector_rotation_agent()
            result = await agent.analyze(
                sector_performance=sector_performance,
                macro_context=macro_context,
                foreign_flow="",
            )
        except Exception as exc:
            logger.error("sector.command.error", error=str(exc))
            await self.send_error(
                interaction,
                title="Phân tích sector thất bại",
                description=f"Không thể phân tích dòng tiền lúc này.\n`{exc}`",
            )
            return

        embed = _build_sector_embed(result, watchlist_filter=watchlist)
        await interaction.followup.send(embed=embed, ephemeral=False)


def _build_sector_embed(
    result: SectorRotationOutput,
    watchlist_filter: list[str] | None = None,
) -> discord.Embed:
    # market_regime is normalized to canonical 4-value enum by the agent.
    # Extra entries here are belt-and-suspenders for unexpected values.
    regime_emoji = {
        "RISK_ON": "🟢",
        "RISK_OFF": "🔴",
        "TRANSITIONING": "🟡",
        "UNCLEAR": "⚪",
        # non-canonical fallbacks
        "LATE_CYCLE_TRANSITION": "🟡",
        "EARLY_RECOVERY": "🟢",
        "MODERATE_GROWTH_EASING_INFLATION": "🟢",
    }
    regime_color = {
        "RISK_ON": discord.Color.green(),
        "RISK_OFF": discord.Color.red(),
        "TRANSITIONING": discord.Color.gold(),
        "UNCLEAR": discord.Color.blurple(),
        # non-canonical fallbacks
        "LATE_CYCLE_TRANSITION": discord.Color.gold(),
        "EARLY_RECOVERY": discord.Color.green(),
        "MODERATE_GROWTH_EASING_INFLATION": discord.Color.green(),
    }

    emoji = regime_emoji.get(result.market_regime, "⚪")
    color = regime_color.get(result.market_regime, discord.Color.blurple())

    embed = discord.Embed(
        title=f"{emoji} Sector Rotation — {result.market_regime}",
        description=result.macro_summary,
        color=color,
    )

    if result.top_rotate_in:
        embed.add_field(name="⬆️ Rotate In", value=", ".join(result.top_rotate_in), inline=True)
    if result.top_rotate_out:
        embed.add_field(name="⬇️ Rotate Out", value=", ".join(result.top_rotate_out), inline=True)

    # Signals — nếu có watchlist, ưu tiên signals có tickers overlap
    signals = result.sector_signals
    if watchlist_filter:
        filtered = [
            s for s in signals
            if any(t in s.key_tickers for t in watchlist_filter)
        ]
        signals = filtered or signals  # fallback full nếu không match

    if signals:
        lines = []
        for s in signals[:5]:
            bar = "█" * round(s.momentum_score * 5) + "░" * (5 - round(s.momentum_score * 5))
            tickers_str = ", ".join(s.key_tickers[:3]) if s.key_tickers else "—"
            lines.append(f"**{s.sector}** `{s.signal}` {bar} — {tickers_str}")
        embed.add_field(name="📊 Signals", value="\n".join(lines), inline=False)

    embed.add_field(name="⚠️ Key Risk", value=result.key_risk, inline=False)
    embed.add_field(name="👀 Next Watch", value=result.next_watch, inline=False)

    conf_label = {"HIGH": "🟢 HIGH", "MEDIUM": "🟡 MEDIUM", "LOW": "🔴 LOW"}.get(
        result.confidence, result.confidence
    )
    embed.set_footer(text=f"Confidence: {conf_label}  ·  stock-agent AI")
    return embed
