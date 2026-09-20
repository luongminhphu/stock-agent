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

from src.ai.schemas import SectorRotationOutput
from src.bot.commands.base import BaseCog
from src.platform.bootstrap import get_quote_service, get_sector_rotation_agent
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
            from src.market.registry import registry as _registry
            from src.market.sector_rotation_service import SectorRotationService

            svc = SectorRotationService(
                quote_service=get_quote_service(),
                registry=_registry,
            )
            # flows là list[SectorFlowData] — market data, bukan AI schema
            flows = await svc.get_sector_flows(watchlist_tickers=watchlist)
            snapshot_date = await svc.get_snapshot_date()

            # Convert SectorFlowData → list[dict] cho agent
            sector_performance = [
                {
                    "sector": f.sector,
                    "avg_change_pct_1d": f.avg_change_pct_1d,
                    "flow_direction": f.flow_direction,
                    "top_movers": f.top_movers,
                    "ticker_count": f.ticker_count,
                }
                for f in flows
            ]

            # Tổng hợp macro_context từ SectorFlowData (market segment data)
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
        description=result.summary or "—",
        color=color,
    )

    if result.top_rotate_in:
        embed.add_field(name="⬆️ Vào vị thế", value=", ".join(result.top_rotate_in), inline=True)
    if result.top_rotate_out:
        embed.add_field(name="⬇️ Thoát vị thế", value=", ".join(result.top_rotate_out), inline=True)

    # Signals — nếu có watchlist, ưu tiên signals có tickers overlap
    # Schema thật: SectorFlow(sector, flow, strength, rationale); ticker overlap nằm ở
    # result.watchlist_crosscheck. Bản cũ đọc key_tickers/momentum_score/signal/macro_summary/
    # key_risk/next_watch (không tồn tại) → /sector_rotation luôn AttributeError (mypy M2).
    signals = result.sector_signals
    crosscheck_sectors: set[str] = set()
    if watchlist_filter:
        for cc in result.watchlist_crosscheck:
            if cc.ticker in watchlist_filter:
                crosscheck_sectors.add(cc.sector)
        filtered = [s for s in signals if s.sector in crosscheck_sectors]
        signals = filtered or signals  # fallback full nếu không match

    if signals:
        lines = []
        for s in signals[:5]:
            filled = round(s.strength * 5)
            bar = "█" * filled + "░" * (5 - filled)
            rationale = f" — {s.rationale[:60]}" if s.rationale else ""
            lines.append(f"**{s.sector}** `{s.flow}` {bar}{rationale}")
        embed.add_field(name="📊 Tín hiệu", value="\n".join(lines), inline=False)

    if result.watchlist_crosscheck:
        cc_lines = [
            f"**{cc.ticker}** ({cc.sector}) {'✅ thuận chiều' if cc.aligned else '⚠️ ngược chiều'}"
            for cc in result.watchlist_crosscheck[:5]
        ]
        embed.add_field(name="👀 Watchlist", value="\n".join(cc_lines), inline=False)

    if result.key_risks:
        embed.add_field(
            name="⚠️ Rủi ro chính",
            value="\n".join(f"• {r}" for r in result.key_risks[:3]),
            inline=False,
        )

    conf_pct = round(result.confidence * 100)
    conf_label = "🟢 HIGH" if conf_pct >= 70 else "🟡 MEDIUM" if conf_pct >= 40 else "🔴 LOW"
    embed.set_footer(text=f"Confidence: {conf_label} ({conf_pct}%)  ·  stock-agent AI")
    return embed
