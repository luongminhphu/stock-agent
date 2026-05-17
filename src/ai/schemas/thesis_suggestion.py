"""
Schemas for ThesisSuggestAgent.

Owner: ai segment.
Public contract returned to callers (api, bot, thesis segment).
"""

from pydantic import BaseModel, Field, field_validator

from src.ai.schemas._base import _coerce_confidence


class SuggestedAssumption(BaseModel):
    assumption_text: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)


class SuggestedCatalyst(BaseModel):
    catalyst_text: str
    expected_timeline: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)


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
