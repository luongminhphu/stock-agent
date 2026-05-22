"""AI structured output schemas.

Owner: ai segment.
All Pydantic models used as structured output contracts for AI agents.
Keep schemas stable — downstream consumers depend on field names.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PostMortemOutput(BaseModel):
    """Structured output from PostMortemService AI call.

    Consumed by: thesis.PostMortemService → ThesisPostMortemReadyEvent
    """
    lesson: str = Field(description="1-2 câu súc tích về nguyên nhân thành công/thất bại")
    pattern: str = Field(default="", description="Pattern label (snake_case), e.g. premature_entry")
    verdict: Literal["CORRECT", "INCORRECT", "MIXED", "INCONCLUSIVE"] = Field(
        default="INCONCLUSIVE",
        description="AI verdict on outcome quality",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    memory_tags: list[str] = Field(
        default_factory=list,
        description="3-5 keyword tags for memory store indexing",
    )
