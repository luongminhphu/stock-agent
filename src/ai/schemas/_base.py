"""
Shared base types for all AI agent schemas.

Owner: ai segment.
Imported by all schema sub-files — keep minimal.

Pydantic models allowed here only if they are:
  - rule-based (not AI-generated), AND
  - used as shared input context across multiple agents.

Agent-specific output schemas belong in their own sub-files.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class Verdict(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


def _coerce_confidence(v: object) -> float:
    """Coerce confidence to float, clamped to [0.0, 1.0]."""
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, f))


class PortfolioRiskNote(BaseModel):
    """Rule-based portfolio risk context. Not AI-generated.

    Computed by PortfolioService / SignalEngineAgent pre-processing.
    Used as structured input context by:
      - SignalEngineAgent  (portfolio_context field in SignalEngineOutput)
      - PortfolioRiskNarratorAgent (primary input)

    Kept in _base to avoid cross-agent import cycles.
    """

    top_concentration: list[str] = Field(
        default_factory=list,
        description="Tickers with weight_pct > 25% - concentration risk.",
    )
    losing_positions: list[str] = Field(
        default_factory=list,
        description="Tickers with pnl_pct < -5%.",
    )
    misaligned_positions: list[str] = Field(
        default_factory=list,
        description="Tickers held but last_verdict is BEARISH.",
    )
    total_pnl_pct: float | None = Field(
        default=None,
        description="Total portfolio PnL %.",
    )
    position_count: int = Field(default=0)
