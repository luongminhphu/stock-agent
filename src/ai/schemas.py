"""
Shared Pydantic output schemas for AI agents.

Owner: ai segment.

Purpose:
  Central location for structured output models used by both agent classes
  and prompt packs. Prevents circular imports that arise when prompt packs
  need the output schema to build AISpec but the agent also imports the prompt.

Import rule:
  - agents/*     : import output schemas FROM here, not from each other.
  - prompts/*    : import output schemas FROM here, not from agents.
  - core / bot / api: may import from here for type hints.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class VerdictOutput(BaseModel):
    """Structured AI verdict produced by IntelligenceVerdictAgent.

    Downstream-safe, stable keys — used by core.engine, bot dispatcher,
    briefing, and evolution feedback store.
    """

    verdict: Literal[
        "BUY_SIGNAL", "SELL_SIGNAL", "HOLD",
        "REVIEW_THESIS", "RISK_ALERT", "WATCH", "NO_ACTION"
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    risk_signals: list[str] = Field(default_factory=list)
    next_watch_items: list[str] = Field(default_factory=list)
    action: str = ""
    reasoning_summary: str = ""
