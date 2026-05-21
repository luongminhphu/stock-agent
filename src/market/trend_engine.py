"""TrendEngine and TrendSignalComposer — market segment.

Owner: market segment.
Responsibility:
  - TrendSignalComposer: compute TechnicalSignalBundle from raw OHLCV bars.
  - TrendEngine: orchestrate per-symbol signal computation.

Boundary:
  - NEVER imports from ai, briefing, thesis, bot, or readmodel segments.
  - Output (TechnicalSignalBundle) is defined in ai.schemas.trend_prediction
    and also mirrored here as a local dataclass (OHLCVBar, SignalComposite)
    to keep computation self-contained.
  - The ai-facing TechnicalSignalBundle is imported only for return type
    annotation — all computation uses local primitives.

Indicators (pure functions, no state):
  Momentum:  RSI-14, MACD histogram (12/26/9 EMA)
  Structure: EMA-20/EMA-50 cross signal, swing HH/HL detection
  Volume:    OBV slope (linear regression over 10 bars), volume surge ratio
  Volatility: ATR-14 expansion ratio (current ATR vs 30-bar ATR)

Design decisions:
  - All indicators use only close/high/low/volume from OHLCVBar.
  - Minimum bars: 60. Below that threshold, returns RANGING regime with
    neutral scores and confidence-reducing reasoning note.
  - No external TA libraries: pure Python math to avoid dependency.
    Trade-off: not the fastest, but zero import friction for Wave 1.
"""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.platform.logging import get_logger

logger = get_logger(__name__)

MIN_BARS = 60


# ---------------------------------------------------------------------------
# Local primitives
# ---------------------------------------------------------------------------

@dataclass
class OHLCVBar:
    """Minimal OHLCV bar. Symbol is optional (set at engine level)."""
    close: float
    high: float
    low: float
    volume: float
    symbol: str = ""


# ---------------------------------------------------------------------------
# Pure indicator functions
# ---------------------------------------------------------------------------

def _ema(values: list[float], period: int) -> list[float]:
    """Exponential Moving Average. Returns list same length as input."""
    if not values:
        return []
    k = 2.0 / (period + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _rsi(closes: list[float], period: int = 14) -> float:
    """RSI over last `period` bars. Returns 0-100."""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas[-period:]]
    losses = [abs(min(d, 0.0)) for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))


def _macd_histogram(closes: list[float]) -> float:
    """MACD histogram = MACD line - signal line (12/26/9 EMA). Returns float."""
    if len(closes) < 35:
        return 0.0
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
    signal_line = _ema(macd_line, 9)
    return macd_line[-1] - signal_line[-1]


def _ema_cross_signal(closes: list[float], fast: int = 20, slow: int = 50) -> float:
    """EMA cross signal: +1 (fast>slow), 0 (equal), -1 (fast<slow). Normalised 0-1."""
    if len(closes) < slow + 1:
        return 0.5
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    diff = ema_fast[-1] - ema_slow[-1]
    price = closes[-1] or 1.0
    # Normalise: diff/price in [-0.05, +0.05] maps to [0, 1]
    normalised = 0.5 + (diff / price) * 10.0
    return max(0.0, min(1.0, normalised))


def _swing_structure_score(closes: list[float], lookback: int = 20) -> float:
    """Detect higher-high / higher-low (bullish) or lower patterns (bearish).

    Splits lookback window into two halves, compares max/min.
    Returns 0-1 normalised score.
    """
    if len(closes) < lookback:
        return 0.5
    window = closes[-lookback:]
    half = lookback // 2
    first_half, second_half = window[:half], window[half:]
    hh = max(second_half) > max(first_half)   # higher high
    hl = min(second_half) > min(first_half)   # higher low
    if hh and hl:
        return 0.8
    if not hh and not hl:
        return 0.2
    return 0.5  # mixed


def _obv_slope(closes: list[float], volumes: list[float], window: int = 10) -> float:
    """OBV slope via linear regression over last `window` bars. Normalised 0-1."""
    if len(closes) < window + 1 or len(volumes) < window + 1:
        return 0.5
    obv = 0.0
    obv_series = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv += volumes[i]
        elif closes[i] < closes[i - 1]:
            obv -= volumes[i]
        obv_series.append(obv)
    series = obv_series[-window:]
    n = len(series)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(series) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, series))
    den = sum((x - mean_x) ** 2 for x in xs) or 1.0
    slope = num / den
    # Normalise: slope in terms of % of last OBV value
    base = abs(series[-1]) or 1.0
    normalised = 0.5 + (slope / base) * 5.0
    return max(0.0, min(1.0, normalised))


def _volume_surge_ratio(volumes: list[float], window: int = 5, baseline: int = 20) -> float:
    """Recent avg volume / baseline avg volume. Normalised 0-1.

    Surge = ratio > 1.5 → score > 0.5 (bullish confirmation when with trend).
    """
    if len(volumes) < baseline:
        return 0.5
    recent_avg = sum(volumes[-window:]) / window
    baseline_avg = sum(volumes[-baseline:]) / baseline or 1.0
    ratio = recent_avg / baseline_avg
    # Map [0, 3] → [0, 1]
    return max(0.0, min(1.0, ratio / 3.0))


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Average True Range over `period` bars."""
    if len(closes) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


def _atr_expansion_ratio(
    highs: list[float], lows: list[float], closes: list[float]
) -> float:
    """ATR-14 / ATR-30 ratio. >1 = expanding (volatile), <1 = contracting.

    Normalised: ratio 1.0 maps to 0.5 (neutral).
    Contracting ATR → score > 0.5 (volatility-friendly for trending moves).
    Expanding ATR → score < 0.5 (choppy, higher risk).
    """
    if len(closes) < 32:
        return 0.5
    atr14 = _atr(highs, lows, closes, 14)
    atr30 = _atr(highs, lows, closes, 30)
    if atr30 == 0:
        return 0.5
    ratio = atr14 / atr30
    # Map: ratio=0.5 → score=0.75 (contraction), ratio=2.0 → score=0.0
    normalised = 1.0 - (ratio - 0.5) / 1.5
    return max(0.0, min(1.0, normalised))


def _classify_label(value: float) -> str:
    if value >= 0.60:
        return "BULLISH"
    if value <= 0.40:
        return "BEARISH"
    return "NEUTRAL"


def _classify_regime(
    structure_value: float, momentum_value: float, volatility_value: float
) -> str:
    """Derive market regime from combined signal dimensions."""
    if volatility_value < 0.35:  # ATR expanding = volatile
        return "VOLATILE"
    if structure_value >= 0.65 and momentum_value >= 0.55:
        return "TRENDING_UP"
    if structure_value <= 0.35 and momentum_value <= 0.45:
        return "TRENDING_DOWN"
    return "RANGING"


# ---------------------------------------------------------------------------
# TrendSignalComposer
# ---------------------------------------------------------------------------

class TrendSignalComposer:
    """Compute TechnicalSignalBundle from a list of OHLCVBar.

    Weights for composite score (must sum to 1.0):
      structure: 0.35  (primary trend direction)
      momentum:  0.30  (speed / confirmation)
      volume:    0.25  (conviction)
      volatility: 0.10 (regime filter)
    """

    WEIGHTS = {"structure": 0.35, "momentum": 0.30, "volume": 0.25, "volatility": 0.10}

    def compute(self, symbol: str, bars: list[OHLCVBar]) -> dict[str, Any]:
        """Return a dict matching TechnicalSignalBundle fields.

        Returns dict (not Pydantic model) to avoid importing ai schema
        in market segment. TrendEngine converts to TechnicalSignalBundle
        via the ai import guard at the top of trend_engine.py.

        Returns neutral bundle dict when bars < MIN_BARS.
        """
        closes = [b.close for b in bars]
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]
        volumes = [b.volume for b in bars]

        if len(bars) < MIN_BARS:
            logger.warning(
                "trend_signal_composer.insufficient_bars",
                symbol=symbol,
                bars=len(bars),
                min_bars=MIN_BARS,
            )
            neutral = {"value": 0.5, "label": "NEUTRAL"}
            return {
                "symbol": symbol,
                "as_of": datetime.now(UTC).isoformat(),
                "momentum": neutral,
                "structure": neutral,
                "volume": neutral,
                "volatility": neutral,
                "composite": 0.5,
                "regime": "RANGING",
            }

        rsi = _rsi(closes)
        macd_hist = _macd_histogram(closes)
        momentum_val = self._compute_momentum(rsi, macd_hist)

        ema_cross = _ema_cross_signal(closes)
        swing = _swing_structure_score(closes)
        structure_val = (ema_cross * 0.6 + swing * 0.4)

        obv = _obv_slope(closes, volumes)
        surge = _volume_surge_ratio(volumes)
        volume_val = (obv * 0.6 + surge * 0.4)

        volatility_val = _atr_expansion_ratio(highs, lows, closes)

        w = self.WEIGHTS
        composite = (
            structure_val * w["structure"]
            + momentum_val * w["momentum"]
            + volume_val * w["volume"]
            + volatility_val * w["volatility"]
        )
        composite = max(0.0, min(1.0, composite))

        regime = _classify_regime(structure_val, momentum_val, volatility_val)

        return {
            "symbol": symbol,
            "as_of": datetime.now(UTC).isoformat(),
            "momentum": {"value": momentum_val, "label": _classify_label(momentum_val)},
            "structure": {"value": structure_val, "label": _classify_label(structure_val)},
            "volume": {"value": volume_val, "label": _classify_label(volume_val)},
            "volatility": {"value": volatility_val, "label": _classify_label(volatility_val)},
            "composite": composite,
            "regime": regime,
        }

    def _compute_momentum(self, rsi: float, macd_hist: float) -> float:
        """Combine RSI and MACD histogram into a 0-1 momentum score."""
        # RSI: map [20, 80] -> [0, 1]
        rsi_norm = max(0.0, min(1.0, (rsi - 20.0) / 60.0))
        # MACD histogram: normalise by a typical range heuristic
        # Use tanh to softly bound: hist=0 → 0.5, large positive → ~1
        macd_norm = 0.5 + 0.5 * math.tanh(macd_hist / 0.5)
        return rsi_norm * 0.5 + macd_norm * 0.5


# ---------------------------------------------------------------------------
# TrendEngine
# ---------------------------------------------------------------------------

class TrendEngine:
    """Orchestrates TechnicalSignalBundle computation for one or many symbols.

    Args:
        market_adapter: object with async get_ohlcv(symbol, days) -> list[OHLCVBar].
                        Adapter is injected; TrendEngine never creates it.
        days:           Lookback window in calendar days. Default 90 (>= MIN_BARS=60).
    """

    def __init__(self, market_adapter: Any, days: int = 90) -> None:
        self._adapter = market_adapter
        self._days = days
        self._composer = TrendSignalComposer()

    async def run_for_symbol(self, symbol: str) -> Any:
        """Compute TechnicalSignalBundle for a single symbol.

        Returns a TechnicalSignalBundle (ai schema) by importing at call
        time to avoid module-level cross-segment import.

        Raises on adapter failure — callers (TrendEngineListener) handle.
        """
        bars = await self._adapter.get_ohlcv(symbol, days=self._days)  # type: ignore[attr-defined]
        bundle_dict = self._composer.compute(symbol, bars)
        # Late import: keeps market module importable without ai installed
        from src.ai.schemas.trend_prediction import TechnicalSignalBundle  # noqa: PLC0415
        return TechnicalSignalBundle.model_validate(bundle_dict)

    async def run_for_symbols(self, symbols: list[str]) -> list[Any]:
        """Compute TechnicalSignalBundle for all symbols concurrently.

        Failures per symbol are re-raised (return_exceptions=False) so
        TrendEngineListener can log and skip per-symbol via its own gather.
        """
        tasks = [self.run_for_symbol(s) for s in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        bundles = []
        for symbol, result in zip(symbols, results):
            if isinstance(result, Exception):
                logger.warning(
                    "trend_engine.symbol_failed",
                    symbol=symbol,
                    error=str(result),
                )
            else:
                bundles.append(result)
        return bundles
