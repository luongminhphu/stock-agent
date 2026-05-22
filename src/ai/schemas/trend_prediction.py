"""TrendPrediction schema — AI segment output contract.

Owner: ai segment.
Produced by: TrendReasoningAgent (src/ai/agents/trend_reasoning.py)
Consumed by:
  - TrendPredictionStore.save()  (readmodel)
  - BriefingService._run_trend_predictions()  (briefing)
  - TrendEngineListener._analyze_one()  (ai, event adapter)
  - bot commands, API routes (downstream)

Boundary:
  - Schema lives in ai segment.
  - TechnicalSignalBundle lives here too — it is the structured input
    that TrendReasoningAgent receives. Market segment produces the bundle
    but does NOT import this file; it defines its own dataclass copy in
    market/trend_engine.py (same fields, avoids cross-segment import).
  - This file has NO imports from market, thesis, or briefing segments.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Predictions older than this are considered stale (e.g. from a previous
# session or a cached result that was never refreshed).
_STALE_THRESHOLD = timedelta(minutes=5)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TrendVerdict(StrEnum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    WATCH = "WATCH"
    REDUCE = "REDUCE"
    STRONG_SELL = "STRONG_SELL"


class TrendDirection(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    SIDEWAYS = "SIDEWAYS"


class TrendHorizon(StrEnum):
    SHORT_TERM = "SHORT_TERM"   # 1-5 ngày
    MID_TERM = "MID_TERM"       # 2-4 tuần


class TrendRegime(StrEnum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"


class SignalLabel(StrEnum):
    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"


# ---------------------------------------------------------------------------
# Technical input bundle (AI-facing view)
# ---------------------------------------------------------------------------

class SignalScore(BaseModel):
    """Normalised 0–1 score for a single technical dimension.

    value=0.0 → strongly bearish; 0.5 → neutral; 1.0 → strongly bullish.
    label is derived from value by the producer (TrendSignalComposer).
    """
    value: float = Field(ge=0.0, le=1.0)
    label: SignalLabel

    @field_validator("value", mode="before")
    @classmethod
    def _clamp(cls, v: object) -> float:
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.5
        return max(0.0, min(1.0, f))


class TechnicalSignalBundle(BaseModel):
    """Structured technical context fed into TrendReasoningAgent.

    Produced by market.TrendSignalComposer. Passed verbatim to
    TrendReasoningAgent.analyze() — no raw OHLCV data is forwarded
    so the AI cannot hallucinate price targets.
    """
    symbol: str
    as_of: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Four orthogonal signal dimensions
    momentum: SignalScore    # RSI-14, MACD histogram
    structure: SignalScore   # EMA 20/50 cross, HH/HL swing pattern
    volume: SignalScore      # OBV slope, volume surge ratio
    volatility: SignalScore  # ATR expansion/contraction

    # Weighted composite (0–1) and regime label
    composite: float = Field(ge=0.0, le=1.0)
    regime: TrendRegime


# ---------------------------------------------------------------------------
# AI output
# ---------------------------------------------------------------------------

class TrendPrediction(BaseModel):
    """Structured verdict produced by TrendReasoningAgent.

    Stored opaquely in TrendPredictionStore (no model_dump needed).
    Downstream consumers access fields via getattr — store never imports
    this type directly to avoid cross-segment coupling.

    Guardrails:
      - confidence is hard-capped at 0.85 to prevent overconfidence.
      - reasoning is capped at 200 chars so it fits in Discord embeds.
    """
    symbol: str
    verdict: TrendVerdict
    direction: TrendDirection
    confidence: float = Field(ge=0.0, le=0.85,
        description="Hard cap 0.85 — prevents overconfidence.")
    horizon: TrendHorizon
    risk_signals: list[str] = Field(
        default_factory=list,
        description="Up to 4 short risk labels, e.g. 'RSI overbought'.",
        max_length=4,
    )
    next_watch: list[str] = Field(
        default_factory=list,
        description="Up to 3 price/event triggers to monitor next.",
        max_length=3,
    )
    reasoning: str = Field(
        default="",
        description="≤ 200 chars. One-sentence rationale for the verdict.",
        max_length=200,
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, v: object) -> float:
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.3
        return max(0.0, min(0.85, f))

    @field_validator("reasoning", mode="before")
    @classmethod
    def _truncate_reasoning(cls, v: object) -> str:
        if not isinstance(v, str):
            return ""
        return v[:200]

    # ------------------------------------------------------------------
    # Convenience helpers for downstream formatters
    # ------------------------------------------------------------------

    @property
    def is_stale(self) -> bool:
        """True when this prediction is older than _STALE_THRESHOLD (5 min).

        Used by bot embeds to show a ⚠️ stale warning when the cached
        result is from a previous session or was never refreshed.
        """
        age = datetime.now(UTC) - self.generated_at
        return age > _STALE_THRESHOLD

    @property
    def is_actionable(self) -> bool:
        """True when verdict warrants immediate attention (BUY/SELL signals)."""
        return self.verdict in (
            TrendVerdict.STRONG_BUY,
            TrendVerdict.BUY,
            TrendVerdict.REDUCE,
            TrendVerdict.STRONG_SELL,
        )

    @property
    def emoji(self) -> str:
        """Discord-friendly emoji for verdict."""
        return {
            TrendVerdict.STRONG_BUY: "🟢🟢",
            TrendVerdict.BUY: "🟢",
            TrendVerdict.HOLD: "⚪",
            TrendVerdict.WATCH: "👁️",
            TrendVerdict.REDUCE: "🔴",
            TrendVerdict.STRONG_SELL: "🔴🔴",
        }.get(self.verdict, "❓")
