"""Briefing DTOs.

Owner: api segment.
Expose BriefOutput to API clients without leaking AI layer internals.
"""
from __future__ import annotations

from pydantic import BaseModel


class BriefResponse(BaseModel):
    headline: str
    sentiment: str
    summary: str
    key_movers: list[str]
    watchlist_alerts: list[str]
    action_items: list[str]
