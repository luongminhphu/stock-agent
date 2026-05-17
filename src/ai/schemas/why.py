"""
Schemas for WhyAgent — explains price movement.

Owner: ai segment.
"""

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from src.ai.schemas._base import _coerce_confidence


class MovementDirection(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    SIDEWAYS = "SIDEWAYS"


class WhyOutput(BaseModel):
    """Structured output from WhyAgent — explains price movement.

    Owner: ai segment.
    change_pct is NOT an AI field — it is injected by WhyService from the
    live quote and attached after AI parse. Embed consumers read it from
    the tuple returned by WhyService.explain().
    """

    ticker: str
    direction: MovementDirection
    magnitude_pct: float = Field(description="Estimated magnitude of movement in %")
    primary_cause: str = Field(description="Main reason for the movement")
    contributing_factors: list[str] = Field(
        default_factory=list,
        description="Secondary contributing factors",
    )
    market_context: str = Field(
        default="",
        description="Broader market context relevant to this movement",
    )
    risk_flags: list[str] = Field(
        default_factory=list,
        description="Rủi ro hoặc yếu tố cần theo dõi sau biến động này (tối đa 3)",
    )
    data_quality: str = Field(
        default="",
        description=(
            "Ghi chú chất lượng dữ liệu: thiếu OHLCV, thiếu tin tức, confidence thấp. "
            "Để trống nếu dữ liệu đầy đủ."
        ),
    )
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)
