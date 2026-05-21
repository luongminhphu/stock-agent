"""TrendEngine — technical signal computation for trend prediction.

Owner: market segment.
Does NOT import from thesis, ai, or bot directly.
Output: TechnicalSignalBundle — consumed by ai segment (TrendReasoningAgent).
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from src.platform.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------

class SignalScore(BaseModel):
    value: float  # 0.0 – 1.0
    label: Literal["BULLISH", "NEUTRAL", "BEARISH"]


class TechnicalSignalBundle(BaseModel):
    symbol: str
    as_of: datetime
    momentum:   SignalScore
    structure:  SignalScore
    volume:     SignalScore
    volatility: SignalScore
    composite:  float  # weighted average
    regime: Literal["TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE"]


# ---------------------------------------------------------------------------
# Signal computation helpers
# ---------------------------------------------------------------------------

def _classify(value: float) -> Literal["BULLISH", "NEUTRAL", "BEARISH"]:
    if value >= 0.6:
        return "BULLISH"
    if value <= 0.4:
        return "BEARISH"
    return "NEUTRAL"


def _ema(prices: list[float], period: int) -> list[float]:
    if not prices:
        return []
    k = 2 / (period + 1)
    emas = [prices[0]]
    for p in prices[1:]:
        emas.append(p * k + emas[-1] * (1 - k))
    return emas


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [d for d in deltas[-period:] if d > 0]
    losses = [-d for d in deltas[-period:] if d < 0]
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd_histogram(closes: list[float]) -> float:
    if len(closes) < 26:
        return 0.0
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    # align: ema26 starts at index 25, ema12 at index 11
    macd_line = [ema12[i] - ema26[i - (len(ema12) - len(ema26))] for i in range(len(ema26), len(ema12))]
    if len(macd_line) < 9:
        return 0.0
    signal = _ema(macd_line, 9)
    return macd_line[-1] - signal[-1]


# ---------------------------------------------------------------------------
# TechnicalSignalComposer
# ---------------------------------------------------------------------------

class TechnicalSignalComposer:
    """Compute 4 signal groups from a list of Candle objects.

    Accepts list[Candle] from OHLCVService directly.
    """

    def compute(self, symbol: str, candles: list) -> TechnicalSignalBundle:
        """candles: list[Candle] from OHLCVService.get_latest_candles()."""
        if len(candles) < 30:
            return self._insufficient_data(symbol)

        closes  = [c.close  for c in candles]
        highs   = [c.high   for c in candles]
        lows    = [c.low    for c in candles]
        volumes = [c.volume for c in candles]

        momentum   = self._momentum(closes)
        structure  = self._structure(closes, highs, lows)
        volume     = self._volume(closes, volumes)
        volatility = self._volatility(highs, lows)

        composite = (
            momentum.value   * 0.35 +
            structure.value  * 0.30 +
            volume.value     * 0.20 +
            volatility.value * 0.15
        )

        regime = self._regime(structure, volatility, composite)

        return TechnicalSignalBundle(
            symbol=symbol,
            as_of=datetime.utcnow(),
            momentum=momentum,
            structure=structure,
            volume=volume,
            volatility=volatility,
            composite=round(composite, 3),
            regime=regime,
        )

    # ------------------------------------------------------------------
    # Individual signal methods
    # ------------------------------------------------------------------

    def _momentum(self, closes: list[float]) -> SignalScore:
        rsi    = _rsi(closes)
        macd_h = _macd_histogram(closes)
        rsi_norm  = max(0.0, min(1.0, (rsi - 30) / 40))
        macd_sign = 1.0 if macd_h > 0 else 0.0
        val = rsi_norm * 0.5 + macd_sign * 0.5
        return SignalScore(value=round(val, 3), label=_classify(val))

    def _structure(self, closes: list[float], highs: list[float], lows: list[float]) -> SignalScore:
        ema20 = _ema(closes, 20)
        ema50 = _ema(closes, 50)
        ema_cross = 1.0 if ema20[-1] > ema50[-1] else 0.0

        recent_highs = highs[-10:]
        recent_lows  = lows[-10:]
        n = len(recent_highs) - 1
        if n <= 0:
            swing_score = 0.5
        else:
            hh = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i] > recent_highs[i - 1])
            hl = sum(1 for i in range(1, len(recent_lows))  if recent_lows[i]  > recent_lows[i - 1])
            swing_score = (hh + hl) / (2 * n)

        val = ema_cross * 0.5 + swing_score * 0.5
        return SignalScore(value=round(val, 3), label=_classify(val))

    def _volume(self, closes: list[float], volumes: list[float]) -> SignalScore:
        if len(volumes) < 20:
            return SignalScore(value=0.5, label="NEUTRAL")
        avg_vol    = sum(volumes[-20:]) / 20
        recent_vol = sum(volumes[-5:]) / 5
        vol_ratio  = min(recent_vol / (avg_vol + 1e-9), 2.0) / 2.0

        n = min(5, len(closes) - 1)
        obv_ups = sum(
            1 for i in range(len(closes) - n, len(closes))
            if closes[i] > closes[i - 1]
        )
        obv_score = obv_ups / n if n > 0 else 0.5

        val = vol_ratio * 0.4 + obv_score * 0.6
        return SignalScore(value=round(val, 3), label=_classify(val))

    def _volatility(self, highs: list[float], lows: list[float]) -> SignalScore:
        if len(highs) < 14:
            return SignalScore(value=0.5, label="NEUTRAL")
        atr_values = [highs[i] - lows[i] for i in range(len(highs))]
        atr_recent = sum(atr_values[-7:]) / 7
        atr_prior  = sum(atr_values[-14:-7]) / 7
        expansion  = min(atr_recent / (atr_prior + 1e-9), 2.0)
        val = max(0.0, min(1.0, expansion / 2))
        return SignalScore(value=round(val, 3), label=_classify(val))

    def _regime(
        self,
        structure: SignalScore,
        volatility: SignalScore,
        composite: float,
    ) -> Literal["TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE"]:
        if volatility.value >= 0.7:
            return "VOLATILE"
        if composite >= 0.6 and structure.label == "BULLISH":
            return "TRENDING_UP"
        if composite <= 0.4 and structure.label == "BEARISH":
            return "TRENDING_DOWN"
        return "RANGING"

    def _insufficient_data(self, symbol: str) -> TechnicalSignalBundle:
        neutral = SignalScore(value=0.5, label="NEUTRAL")
        return TechnicalSignalBundle(
            symbol=symbol,
            as_of=datetime.utcnow(),
            momentum=neutral, structure=neutral,
            volume=neutral, volatility=neutral,
            composite=0.5, regime="RANGING",
        )


# ---------------------------------------------------------------------------
# TrendEngine — orchestrator
# ---------------------------------------------------------------------------

class TrendEngine:
    """Orchestrate: fetch OHLCV candles → compute TechnicalSignalBundle.

    AI reasoning is done in the ai segment (TrendReasoningAgent, Wave 2).
    This engine is purely a market concern.
    """

    def __init__(self, ohlcv_service) -> None:
        self._ohlcv = ohlcv_service
        self._composer = TechnicalSignalComposer()

    async def run_for_symbol(self, symbol: str) -> TechnicalSignalBundle:
        try:
            candles = await self._ohlcv.get_latest_candles(symbol.upper(), n=60)
        except Exception as exc:
            logger.warning("trend_engine.ohlcv_failed", symbol=symbol, error=str(exc))
            return self._composer._insufficient_data(symbol.upper())

        if not candles:
            logger.warning("trend_engine.no_candles", symbol=symbol)
            return self._composer._insufficient_data(symbol.upper())

        return self._composer.compute(symbol.upper(), candles)

    async def run_for_symbols(self, symbols: list[str]) -> list[TechnicalSignalBundle]:
        results = await asyncio.gather(
            *[self.run_for_symbol(s) for s in symbols],
            return_exceptions=True,
        )
        bundles = []
        for symbol, result in zip(symbols, results):
            if isinstance(result, Exception):
                logger.error("trend_engine.symbol_failed", symbol=symbol, error=str(result))
                bundles.append(self._composer._insufficient_data(symbol.upper()))
            else:
                bundles.append(result)
        return bundles
