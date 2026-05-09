"""Briefing DTOs.

Owner: api segment.
Expose BriefOutput to API clients without leaking AI layer internals.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class BriefResponse(BaseModel):
    snapshot_id: int | None = None
    headline: str
    sentiment: str
    summary: str
    key_movers: list[str]
    watchlist_alerts: list[str]
    action_items: list[str]


class FeedbackRequest(BaseModel):
    outcome: Literal["acted", "watching", "skipped"]
