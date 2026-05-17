"""
Shared base types for all AI agent schemas.

Owner: ai segment.
Imported by all schema sub-files — keep minimal, no Pydantic models here.
"""

from enum import StrEnum


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
