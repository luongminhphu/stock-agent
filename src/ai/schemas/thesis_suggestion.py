"""
Schemas for ThesisSuggestAgent.

Owner: ai segment.
Public contract returned to callers (api, bot, thesis segment).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from src.ai.schemas._base import _coerce_confidence


class SuggestedAssumption(BaseModel):
    assumption_text: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)


def _parse_timeline_to_date(timeline: str) -> Optional[date]:
    """Best-effort convert 'Q3 2025', 'H1 2026', 'T6/2025', 'YYYY-MM-DD' → date."""
    if not timeline:
        return None
    t = timeline.strip()
    # Already ISO
    try:
        return date.fromisoformat(t[:10])
    except ValueError:
        pass
    # Q1-Q4 YYYY
    m = re.match(r"Q([1-4])[/ ]?(\d{4})", t, re.I)
    if m:
        q, y = int(m.group(1)), int(m.group(2))
        month = q * 3  # end of quarter
        return date(y, month, 1)
    # H1/H2 YYYY
    m = re.match(r"H([12])[/ ]?(\d{4})", t, re.I)
    if m:
        h, y = int(m.group(1)), int(m.group(2))
        month = 6 if h == 1 else 12
        return date(y, month, 1)
    # T6/2025 or 6/2025
    m = re.match(r"(?:T)?(\d{1,2})[/\-](\d{4})", t)
    if m:
        mo, y = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return date(y, mo, 1)
    return None


class SuggestedCatalyst(BaseModel):
    catalyst_text: str
    expected_timeline: str
    expected_date: Optional[date] = None
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)

    @model_validator(mode="after")
    def derive_expected_date(self) -> "SuggestedCatalyst":
        if self.expected_date is None and self.expected_timeline:
            self.expected_date = _parse_timeline_to_date(self.expected_timeline)
        return self


class ThesisSuggestionResult(BaseModel):
    """Structured output from ThesisSuggestAgent.

    Owner: ai segment.
    Public contract returned to callers (api, bot, thesis segment).
    """

    ticker: str
    title: str
    thesis_type: str = Field(
        default="",
        description="Loại thesis: VALUE, GROWTH, TURNAROUND, TECHNICAL, MACRO",
    )
    summary: str
    assumptions: list[SuggestedAssumption] = Field(default_factory=list)
    catalysts: list[SuggestedCatalyst] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)
    target_horizon: str = Field(
        default="",
        description="Khung thời gian kỳ vọng: SHORT (< 3 tháng), MEDIUM (3-12 tháng), LONG (> 12 tháng)",
    )
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator("assumptions", "catalysts", "invalidation_conditions", mode="before")
    @classmethod
    def ensure_lists(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]
