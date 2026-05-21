"""Trend command cog.

Owner: bot segment — thin adapter only.
Delegates to TrendEngine (market) for signal computation.
Wave 2: swap rule-based fallback for TrendReasoningAgent (ai segment).

Commands:
    /trend <ticker>  — xu hướng tăng/giảm cho 1 mã
"""
from __future__ import annotations

import discord
from discord import app_commands

from src.bot.commands.base import BaseCog
from src.market.trend_engine import TechnicalSignalBundle, TrendEngine
from src.platform.bootstrap import get_ohlcv_service
from src.platform.logging import get_logger

logger = get_logger(__name__)

_VERDICT_META: dict[str, tuple[str, discord.Color]] = {
    "STRONG_BUY":  ("🟢🟢", discord.Color.from_rgb(0, 180, 80)),
    "BUY":         ("🟢",   discord.Color.green()),
    "HOLD":        ("🟡",   discord.Color.gold()),
    "WATCH":       ("🟠",   discord.Color.from_rgb(255, 140, 0)),
    "REDUCE":      ("🔴",   discord.Color.from_rgb(220, 80, 40)),
    "STRONG_SELL": ("🔴🔴", discord.Color.red()),
}

_REGIME_LABEL: dict[str, str] = {
    "TRENDING_UP":   "📈 Uptrend",
    "TRENDING_DOWN": "📉 Downtrend",
    "RANGING":       "➡️  Ranging",
    "VOLATILE":      "⚡ Volatile",
}


class TrendCog(BaseCog):
    """Slash command: /trend"""

    @app_commands.command(
        name="trend",
        description="Phân tích xu hướng tăng/giảm của một mã cổ phiếu",
    )
    @app_commands.describe(ticker="Mã cổ phiếu (VD: HPG, VNM, FPT)")
    async def trend(
        self,
        interaction: discord.Interaction,
        ticker: str,
    ) -> None:
        await interaction.response.defer(ephemeral=False)
        symbol = ticker.strip().upper()

        engine = TrendEngine(get_ohlcv_service())

        try:
            bundle = await engine.run_for_symbol(symbol)
        except Exception as exc:
            logger.error("trend.engine_failed", symbol=symbol, error=str(exc))
            await self.send_error(
                interaction,
                title="Không thể phân tích xu hướng",
                description=f"Lỗi khi lấy dữ liệu **{symbol}**.\n`{exc}`",
            )
            return

        # Wave 1: rule-based verdict (Wave 2: replace with TrendReasoningAgent)
        prediction = _rule_based_prediction(bundle)
        embed = _build_trend_embed(bundle, prediction)
        await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# Rule-based fallback — Wave 1
# Replace this function body in Wave 2 with TrendReasoningAgent call.
# ---------------------------------------------------------------------------

def _rule_based_prediction(bundle: TechnicalSignalBundle):
    from src.ai.prompts.trend_reasoning import TrendPrediction

    c = bundle.composite
    if c >= 0.72:
        verdict, direction = "STRONG_BUY", "UP"
    elif c >= 0.58:
        verdict, direction = "BUY", "UP"
    elif c >= 0.45:
        verdict, direction = "HOLD", "SIDEWAYS"
    elif c >= 0.32:
        verdict, direction = "WATCH", "SIDEWAYS"
    elif c >= 0.20:
        verdict, direction = "REDUCE", "DOWN"
    else:
        verdict, direction = "STRONG_SELL", "DOWN"

    risks: list[str] = []
    if bundle.momentum.label == "BEARISH":
        risks.append("RSI/MACD momentum yếu")
    if bundle.volume.label == "BEARISH":
        risks.append("Volume sụt giảm")
    if bundle.structure.label == "BEARISH":
        risks.append("EMA20 dưới EMA50")
    if bundle.volatility.label == "BULLISH" and verdict in ("REDUCE", "STRONG_SELL"):
        risks.append("ATR mở rộng — rủi ro biến động cao")

    next_watch: list[str] = []
    if bundle.regime == "RANGING":
        next_watch.append("Chờ breakout khỏi vùng tích lũy")
    if bundle.momentum.label == "NEUTRAL":
        next_watch.append("Theo dõi MACD cross confirm")
    if bundle.structure.label == "NEUTRAL":
        next_watch.append("Theo dõi EMA20/50 cross")

    confidence = min(0.85, abs(c - 0.5) * 2 * 0.85)

    return TrendPrediction(
        symbol=bundle.symbol,
        verdict=verdict,
        direction=direction,
        confidence=round(confidence, 2),
        horizon="SHORT_TERM",
        risk_signals=risks[:5],
        next_watch=next_watch[:3],
        reasoning=f"Composite {c:.2f} · Regime {bundle.regime} · Rule-based (Wave 1)",
    )


# ---------------------------------------------------------------------------
# Embed builder
# ---------------------------------------------------------------------------

def _build_trend_embed(bundle: TechnicalSignalBundle, pred) -> discord.Embed:
    icon, colour = _VERDICT_META.get(pred.verdict, ("⚪", discord.Color.greyple()))
    regime_label = _REGIME_LABEL.get(bundle.regime, bundle.regime)

    embed = discord.Embed(
        title=f"{icon} {bundle.symbol} — {pred.verdict}",
        description=f"**{regime_label}** · Horizon: `{pred.horizon.replace('_', ' ').title()}`",
        color=colour,
    )

    def _bar(val: float) -> str:
        filled = round(val * 10)
        return "█" * filled + "░" * (10 - filled)

    embed.add_field(
        name="📊 Technical Signals",
        value=(
            f"`Momentum  ` {_bar(bundle.momentum.value)} `{bundle.momentum.label}`\n"
            f"`Structure ` {_bar(bundle.structure.value)} `{bundle.structure.label}`\n"
            f"`Volume    ` {_bar(bundle.volume.value)} `{bundle.volume.label}`\n"
            f"`Volatility` {_bar(bundle.volatility.value)} `{bundle.volatility.label}`"
        ),
        inline=False,
    )

    embed.add_field(
        name="🎯 Verdict",
        value=(
            f"`{pred.verdict}` · Direction: **{pred.direction}**"
            f" · Confidence: `{pred.confidence:.0%}`"
        ),
        inline=False,
    )

    if pred.risk_signals:
        embed.add_field(
            name="⚠️ Risk Signals",
            value="\n".join(f"• {r}" for r in pred.risk_signals),
            inline=False,
        )

    if pred.next_watch:
        embed.add_field(
            name="👁 Next Watch",
            value="\n".join(f"• {w}" for w in pred.next_watch),
            inline=False,
        )

    embed.add_field(
        name="💬 Reasoning",
        value=pred.reasoning or "—",
        inline=False,
    )

    embed.set_footer(
        text=(
            f"Composite: {bundle.composite:.2f}"
            f" · stock-agent"
            f" · {bundle.as_of.strftime('%H:%M %d/%m/%Y')} UTC"
        )
    )
    return embed
