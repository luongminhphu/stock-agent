"""Read-model DTOs — pure Pydantic, no ORM imports.

Owner: readmodel segment.
These are the output contracts for all dashboard / read queries.
Write-side domain models (thesis.models) must NOT be imported here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------


class PricePoint(BaseModel):
    """Single price observation in a time series."""
    ts: datetime
    price: float


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class ThesisSummaryRow(BaseModel):
    """One row in the dashboard thesis table."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker: str
    title: str
    status: str
    score: float | None
    entry_price: float | None
    target_price: float | None
    stop_loss: float | None
    upside_pct: float | None
    risk_reward: float | None
    current_price: float | None          # injected from market segment
    pnl_pct: float | None                # (current - entry) / entry * 100
    last_verdict: str | None             # latest ThesisReview.verdict
    last_reviewed_at: datetime | None
    created_at: datetime
    assumption_count: int
    invalid_assumption_count: int
    catalyst_count: int
    triggered_catalyst_count: int


class DashboardResponse(BaseModel):
    """Full dashboard payload."""
    user_id: str
    generated_at: datetime
    total_theses: int
    active_count: int
    invalidated_count: int
    closed_count: int
    avg_score: float | None
    theses: list[ThesisSummaryRow]


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


class LeaderboardEntry(BaseModel):
    """One entry in the thesis leaderboard."""
    model_config = ConfigDict(from_attributes=True)

    rank: int
    thesis_id: int
    ticker: str
    title: str
    score: float | None
    pnl_pct: float | None
    last_verdict: str | None
    status: str
    created_at: datetime


class LeaderboardResponse(BaseModel):
    user_id: str
    sort_by: Literal["score", "pnl"]  # which metric drives ranking
    entries: list[LeaderboardEntry]


# ---------------------------------------------------------------------------
# Thesis timeline
# ---------------------------------------------------------------------------


class TimelineEventKind(str):
    CREATED = "created"
    REVIEWED = "reviewed"
    ASSUMPTION_UPDATED = "assumption_updated"
    CATALYST_TRIGGERED = "catalyst_triggered"
    INVALIDATED = "invalidated"
    CLOSED = "closed"
    SNAPSHOT = "snapshot"


class TimelineEvent(BaseModel):
    """Single event on a thesis timeline."""
    kind: str
    ts: datetime
    summary: str          # human-readable one-liner, built by query
    detail: dict | None   # arbitrary extra payload (e.g. verdict, score)


class ThesisTimelineResponse(BaseModel):
    thesis_id: int
    ticker: str
    title: str
    events: list[TimelineEvent]  # ordered oldest → newest


# ---------------------------------------------------------------------------
# Watchlist snapshot (used by dashboard watchlist panel)
# ---------------------------------------------------------------------------


class WatchlistSnapshotRow(BaseModel):
    ticker: str
    note: str | None
    thesis_id: int | None
    thesis_title: str | None
    thesis_status: str | None
    current_price: float | None
    added_at: datetime
