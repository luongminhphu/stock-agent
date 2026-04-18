"""Thesis DTOs.

Owner: api segment.
No SQLAlchemy objects cross this boundary.
"""
from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, field_validator


class ThesisReviewResponse(BaseModel):
    """Response for a single ThesisReview record."""

    id: int
    thesis_id: int
    verdict: str
    confidence: float
    reasoning: str
    risk_signals: list[str]
    next_watch_items: list[str]
    reviewed_at: datetime
    reviewed_price: float | None

    @field_validator("risk_signals", "next_watch_items", mode="before")
    @classmethod
    def parse_json_list(cls, v: object) -> list[str]:
        """ORM stores these as JSON strings; API exposes them as real lists."""
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, list) else [v]
            except json.JSONDecodeError:
                return [v]
        if isinstance(v, list):
            return v
        return []

    model_config = {"from_attributes": True}


class ThesisReviewListResponse(BaseModel):
    thesis_id: int
    reviews: list[ThesisReviewResponse]
    total: int
