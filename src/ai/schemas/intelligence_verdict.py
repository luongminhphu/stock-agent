"""
src/ai/schemas/intelligence_verdict.py

Schema for the Intelligence Verdict agent output.
Canonical location — import from src.ai.schemas import VerdictOutput.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class VerdictOutput(BaseModel):
    verdict: Literal[
        "BUY_SIGNAL",
        "SELL_SIGNAL",
        "HOLD",
        "REVIEW_THESIS",
        "RISK_ALERT",
        "NO_ACTION",
    ]
    confidence: float = Field(..., ge=0.0, le=1.0)
    risk_signals: list[str] = Field(default_factory=list)
    next_watch_items: list[str] = Field(default_factory=list)
    action: str
    reasoning_summary: str
    sources: list[str] = Field(default_factory=list)

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))
