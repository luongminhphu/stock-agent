"""Read-model DTOs — pure Pydantic, no ORM imports.

Owner: readmodel segment.
These are the output contracts for all dashboard / read queries.
Write-side domain models (thesis.models) must NOT be imported here.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
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
    direction: str | None = None
    score: float | None

    # Score context — populated by dashboard_service, sourced from thesis.scoring_service
    score_tier: str | None = None
    score_tier_icon: str | None = None
    score_breakdown: dict[str, float] | None = None

    entry_price: float | None
    target_price: float | None
    stop_loss: float | None
    upside_pct: float | None
    risk_reward: float | None
    current_price: float | None
    pnl_pct: float | None
    last_verdict: str | None
    last_reviewed_at: datetime | None
    created_at: datetime
    assumption_count: int
    invalid_assumption_count: int
    catalyst_count: int
    triggered_catalyst_count: int
    change: float | None = None
    change_pct: float | None = None
    volume: int | None = None
    is_ceiling: bool | None = None
    is_floor: bool | None = None


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
    sort_by: Literal["score", "pnl"]
    entries: list[LeaderboardEntry]


# ---------------------------------------------------------------------------
# Thesis timeline
# ---------------------------------------------------------------------------


class TimelineEventKind(StrEnum):
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
    summary: str
    detail: dict | None


class ThesisTimelineResponse(BaseModel):
    thesis_id: int
    ticker: str
    title: str
    events: list[TimelineEvent]


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


# ---------------------------------------------------------------------------
# Review timeline (focused — 5 AI reviews gần nhất)
# ---------------------------------------------------------------------------


class ReviewTimelineItem(BaseModel):
    """Single AI review entry in the focused review timeline."""

    review_id: int
    reviewed_at: datetime
    verdict: str
    confidence: float
    confidence_pct: int
    reasoning: str | None = None
    risk_signals: list[str] = []
    next_watch_items: list[str] = []
    reviewed_price: float | None = None


class ReviewTimelineResponse(BaseModel):
    thesis_id: int
    ticker: str
    title: str
    items: list[ReviewTimelineItem]
    total: int


# ---------------------------------------------------------------------------
# Conviction Score Timeline
# ---------------------------------------------------------------------------


class ConvictionBreakdown(BaseModel):
    """Per-dimension score breakdown at a single point in time (0–100 scale)."""

    assumption_health: float
    catalyst_progress: float
    risk_reward: float
    review_confidence: float


class ConvictionPoint(BaseModel):
    """One data-point on the Conviction Score Timeline."""

    model_config = ConfigDict(frozen=False)

    snapshot_id: int
    snapshotted_at: datetime
    score: float
    score_tier: str
    score_tier_icon: str
    breakdown: ConvictionBreakdown | None = None
    verdict: str | None = None
    confidence: float | None = None
    price: float | None = None
    pnl_pct: float | None = None
    price_filled: bool = False
    kind: str = "snapshot"
    reasoning_summary: str | None = None
    risk_signals: list[str] = []


class ConvictionTrend(StrEnum):
    IMPROVING = "improving"
    DECLINING = "declining"
    STABLE = "stable"
    INSUFFICIENT_DATA = "insufficient_data"


class ConvictionTimelineResponse(BaseModel):
    thesis_id: int
    ticker: str
    title: str
    points: list[ConvictionPoint]
    trend: str
    latest_score: float | None
    earliest_score: float | None
    total: int
    entry_price: float | None = None


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------


class PortfolioPositionRow(BaseModel):
    thesis_id: int
    ticker: str
    title: str
    status: str
    quantity: float | None
    entry_price: float | None
    current_price: float | None
    pnl_pct: float | None
    pnl_abs: float | None
    cost_basis: float | None
    market_value: float | None
    weight_pct: float | None
    last_verdict: str | None
    score: float | None
    score_tier: str | None
    score_tier_icon: str | None
    change_pct: float | None = None


class PortfolioSummary(BaseModel):
    user_id: str
    generated_at: datetime
    total_cost_basis: float | None
    total_market_value: float | None
    total_pnl_abs: float | None
    total_pnl_pct: float | None
    position_count: int
    winning_count: int
    losing_count: int
    neutral_count: int
    has_quantity_data: bool
    positions: list[PortfolioPositionRow]


# ---------------------------------------------------------------------------
# Attention Panel — “Việc cần làm hôm nay”
# ---------------------------------------------------------------------------


class AttentionUrgency(StrEnum):
    CRITICAL = "critical"   # cần hành động ngay (stop_loss gần, alert critical)
    HIGH = "high"           # quan trọng, nên xử lý hôm nay (overdue review, catalyst sắp)
    MEDIUM = "medium"       # theo dõi thêm (alert thường, catalyst xa hơn)


class AttentionItem(BaseModel):
    """Một mục cần chú ý trong ngày — aggregated từ nhiều nguồn.

    kind:
      'triggered_alert'    — alert đã fire, chưa dismiss
      'overdue_review'     — thesis active, không có AI review trong >14 ngày
      'upcoming_catalyst'  — catalyst PENDING trong vòng 72h
      'stop_loss_proximity'— giá hiện tại cách stop_loss trong vòng 3%

    urgency:
      'critical' — hành động ngay
      'high'     — xử lý trong ngày
      'medium'   — theo dõi

    metadata: tuỳ theo kind — ví dụ {'days_overdue': 21} hoặc {'distance_pct': 1.8}
    """

    kind: str
    ticker: str
    thesis_id: int | None = None
    message: str
    urgency: str                        # AttentionUrgency value
    ts: datetime                        # thời điểm phát sinh (alert.triggered_at, catalyst.deadline, ...)
    metadata: dict | None = None


class AttentionPanelResponse(BaseModel):
    """Response cho panel 'Việc cần làm hôm nay'.

    items: sorted critical → high → medium, stable sort by ts desc trong mỗi level.
    total: số items trả về (đã áp dụng limit).
    generated_at: server time khi query chạy.
    """

    user_id: str
    generated_at: datetime
    items: list[AttentionItem]
    total: int


# ---------------------------------------------------------------------------
# Recent AI Reviews — cross-thesis surface of SignalEngine loop output
# ---------------------------------------------------------------------------


class RecentReviewRow(BaseModel):
    """One AI review record, joined with thesis metadata.

    Produced by RecentReviewsStore.get_recent().
    Consumed by: bot /reviews command, API /readmodel/reviews,
    briefing context injection.
    """

    review_id: int
    thesis_id: int
    ticker: str
    thesis_title: str
    thesis_status: str          # ThesisStatus value: active / invalidated / closed / paused
    verdict: str                # ReviewVerdict value: BULLISH / BEARISH / NEUTRAL / WATCHLIST
    confidence: float           # 0.0–1.0
    confidence_pct: int         # round(confidence * 100)
    reasoning: str | None = None
    summary: str | None = None
    risk_signals: list[str] = []
    next_watch_items: list[str] = []
    reviewed_at: datetime
    reviewed_price: float | None = None


class RecentReviewsResponse(BaseModel):
    """Response envelope for RecentReviewsStore.get_recent()."""

    user_id: str
    since_hours: int
    ticker_filter: str | None
    generated_at: datetime
    rows: list[RecentReviewRow]
    total: int
