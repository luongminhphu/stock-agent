"""Trend command cog — Wave 2.

Owner: bot segment — thin adapter only.
Flow:
    /trend <ticker>
        → TrendEngine.run_for_symbol()       [market segment]
        → TrendReasoningAgent.analyze()       [ai segment]
        → TrendPrediction embed               [bot renders]

Fallback: if AIError, falls back to rule-based verdict (Wave 1 logic).
"""
from __future__ import annotations

import discord
from discord import app_commands

from src.ai.agents.trend_reasoning import TrendReasoningAgent
from src.ai.client import AIError
from src.ai.schemas.trend_prediction import TechnicalSignalBundle, TrendPrediction
from src.bot.commands.base import BaseCog
from src.market.trend_engine import TrendEngine
from src.platform.bootstrap import get_ohlcv_service, get_trend_reasoning_agent
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

    @app_commands.command(
        name="trend",
        description="Phân tích xu hướng tăng/giảm của một mã cổ phiếu (AI)",
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

        # Wave 2: AI reasoning — fallback to rule-based on AIError
        agent: TrendReasoningAgent | None = get_trend_reasoning_agent()
        prediction: TrendPrediction

        if agent is not None:
            try:
                prediction = await agent.analyze(bundle)
            except AIError as exc:
                logger.warning(
                    "trend.ai_fallback",
                    symbol=symbol,
                    error=str(exc),
                )
                prediction = _rule_based_prediction(bundle)
                prediction = prediction.model_copy(
                    update={"reasoning": f"[Fallback rule-based] {prediction.reasoning}"}
                )
        else:
            logger.warning("trend.no_agent", symbol=symbol)
            prediction = _rule_based_prediction(bundle)

        embed = _build_trend_embed(bundle, prediction)
        await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# Rule-based fallback
# ---------------------------------------------------------------------------

def _rule_based_prediction(bundle: TechnicalSignalBundle) -> TrendPrediction:
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
        reasoning=f"Composite {c:.2f} · Regime {bundle.regime} · Rule-based fallback",
    )


# ---------------------------------------------------------------------------
# Embed builder
# ---------------------------------------------------------------------------

def _build_trend_embed(bundle: TechnicalSignalBundle, pred: TrendPrediction) -> discord.Embed:
    icon, colour = _VERDICT_META.get(pred.verdict, ("⚪", discord.Color.greyple()))
    regime_label = _REGIME_LABEL.get(bundle.regime, bundle.regime)
    stale_flag = " ⚠️ stale" if pred.is_stale else ""

    embed = discord.Embed(
        title=f"{icon} {bundle.symbol} — {pred.verdict}",
        description=(
            f"**{regime_label}**{stale_flag}"
            f" · Horizon: `{pred.horizon.replace('_', ' ').title()}`"
        ),
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
            f" · stock-agent AI"
            f" · {bundle.as_of.strftime('%H:%M %d/%m/%Y')} UTC"
        )
    )
    return embed
