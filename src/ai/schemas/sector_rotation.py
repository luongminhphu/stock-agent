"""
Schemas for SectorRotationAgent.

Owner: ai segment.
Input: raw market data (OHLCV + foreign flow + sector indices).
Output feeds: signal_engine context, briefing narrative, readmodel sector view.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from src.ai.schemas._base import _coerce_confidence


class FlowDirection(StrEnum):
    INFLOW = "INFLOW"
    OUTFLOW = "OUTFLOW"
    NEUTRAL = "NEUTRAL"


class RiskRegime(StrEnum):
    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    NEUTRAL = "NEUTRAL"


class SectorFlow(BaseModel):
    """Flow signal for a single sector."""

    sector: str
    flow: FlowDirection = FlowDirection.NEUTRAL
    strength: float = Field(default=0.0, ge=0.0, le=1.0, description="Signal strength 0-1")
    rationale: str = Field(default="", description="Brief rationale for this sector's flow")

    @model_validator(mode="before")
    @classmethod
    def coerce_aliases(cls, data: Any) -> Any:
        """Coerce AI alias field names -> canonical flow/strength."""
        if not isinstance(data, dict):
            return data
        d = dict(data)

        if "flow" not in d:
            for alias in ("direction", "flow_direction", "signal", "trend", "momentum_direction"):
                if alias in d:
                    d["flow"] = d[alias]
                    break

        if "flow" in d and not isinstance(d["flow"], FlowDirection):
            raw = str(d["flow"]).upper().strip()
            _flow_map: dict[str, str] = {
                "INFLOW": "INFLOW", "OUTFLOW": "OUTFLOW", "NEUTRAL": "NEUTRAL",
                "POSITIVE": "INFLOW", "UP": "INFLOW", "BULLISH": "INFLOW",
                "BUY": "INFLOW", "STRONG": "INFLOW", "RISING": "INFLOW",
                "NEGATIVE": "OUTFLOW", "DOWN": "OUTFLOW", "BEARISH": "OUTFLOW",
                "SELL": "OUTFLOW", "WEAK": "OUTFLOW", "FALLING": "OUTFLOW",
            }
            d["flow"] = _flow_map.get(raw, "NEUTRAL")

        if "strength" not in d:
            for alias in (
                "signal_strength", "score", "weight", "momentum_score",
                "avg_return", "performance", "change_pct", "return_pct",
            ):
                if alias in d:
                    d["strength"] = d[alias]
                    break

        return d

    @field_validator("strength", mode="before")
    @classmethod
    def coerce_strength(cls, v: object) -> float:
        try:
            f = abs(float(v))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, f))


class WatchlistCrosscheck(BaseModel):
    """Cross-check result for a watchlist ticker against sector rotation."""

    ticker: str
    sector: str
    aligned: bool
    note: str = Field(default="", description="Brief note on alignment or misalignment")


class SectorRotationOutput(BaseModel):
    """Structured output from SectorRotationAgent.

    Owner: ai segment.
    Input: raw market data (OHLCV + foreign flow + sector indices).
    Output feeds: signal_engine context, briefing narrative, readmodel sector view.
    """

    market_regime: RiskRegime
    sector_signals: list[SectorFlow] = Field(default_factory=list)
    top_rotate_in: list[str] = Field(
        default_factory=list,
        description="Sectors with strongest inflow signal - rotate INTO",
    )
    top_rotate_out: list[str] = Field(
        default_factory=list,
        description="Sectors with strongest outflow signal - rotate OUT OF",
    )
    watchlist_crosscheck: list[WatchlistCrosscheck] = Field(
        default_factory=list,
        description="How watchlist tickers align with current rotation",
    )
    key_risks: list[str] = Field(
        default_factory=list,
        description="Macro/market risks relevant to current rotation",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(default="", description="1-2 sentence rotation narrative")

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator(
        "sector_signals", "top_rotate_in", "top_rotate_out",
        "watchlist_crosscheck", "key_risks",
        mode="before",
    )
    @classmethod
    def ensure_lists(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]

    @model_validator(mode="after")
    def normalize_model_output(self) -> "SectorRotationOutput":
        """Normalize verbose/non-canonical market_regime values from model."""
        if not self.top_rotate_in and self.sector_signals:
            self.top_rotate_in = [
                s.sector
                for s in sorted(
                    [s for s in self.sector_signals if s.flow == FlowDirection.INFLOW],
                    key=lambda s: -s.strength,
                )[:3]
            ]
        if not self.top_rotate_out and self.sector_signals:
            self.top_rotate_out = [
                s.sector
                for s in sorted(
                    [s for s in self.sector_signals if s.flow == FlowDirection.OUTFLOW],
                    key=lambda s: -s.strength,
                )[:3]
            ]
        if self.confidence == 0.0 and self.sector_signals:
            strengths = [s.strength for s in self.sector_signals if s.strength > 0]
            if strengths:
                self.confidence = sum(strengths) / len(strengths)
        return self
