"""PostMortemOutput — structured AI output for thesis post-mortem analysis.

Owner: ai segment.
Consumed by: thesis.PostMortemService → ThesisPostMortemReadyEvent.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

PostMortemVerdict = Literal["CORRECT", "INCORRECT", "MIXED", "INCONCLUSIVE"]


class PostMortemOutput(BaseModel):
    """AI-extracted lesson after a thesis closes."""

    lesson: str = Field(
        description="1-2 câu súc tích rút ra bài học cụ thể từ thesis này."
    )
    pattern: str = Field(
        description=(
            "Nhãn pattern hành vi nhà đầu tư dạng snake_case. "
            "Ví dụ: premature_entry, catalyst_miss, correct_breakout, "
            "stop_loss_discipline, overconfidence, position_sizing_error."
        )
    )
    verdict: PostMortemVerdict = Field(
        description="Đánh giá tổng thể về quyết định đầu tư."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Mức độ tin cậy của AI vào đánh giá này (0.0–1.0).",
    )
    memory_tags: list[str] = Field(
        min_length=3,
        max_length=5,
        description="3-5 từ khoá ngắn để index vào memory (ticker, pattern, market context).",
    )

    @field_validator("pattern")
    @classmethod
    def _snake_case(cls, v: str) -> str:
        return v.strip().lower().replace(" ", "_")

    @field_validator("memory_tags")
    @classmethod
    def _strip_tags(cls, v: list[str]) -> list[str]:
        return [t.strip().lower() for t in v if t.strip()]
