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

    # Score context — populated by dashboard_service, sourced from thesis.scoring_service
    # score_tier:      e.g. "Critical" / "Weak" / "Moderate" / "Healthy" / "Strong"
    # score_tier_icon: matching emoji e.g. "🔴" / "🟠" / "🟡" / "🟢" / "📎"
    # score_breakdown: per-dimension contributions, keys match ScoringService.compute_with_breakdown()
    #                  Only populated when full ORM graph is loaded (e.g. thesis detail).
    #                  None in list views where we avoid loading assumptions/catalysts.
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
    change: float | None = None  # thay đổi tuyệt đối so với hôm qua (VND)
    change_pct: float | None = None  # thay đổi % so với ref_price
    volume: int | None = None  # khối lượng khớp
    is_ceiling: bool | None = None  # đang trần?
    is_floor: bool | None = None  # đang sàn?


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
    summary: str  # human-readable one-liner, built by query
    detail: dict | None  # arbitrary extra payload (e.g. verdict, score)


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


# ---------------------------------------------------------------------------
# Review timeline (focused — 5 AI reviews gần nhất)
# ---------------------------------------------------------------------------


class ReviewTimelineItem(BaseModel):
    """Single AI review entry in the focused review timeline."""

    review_id: int
    reviewed_at: datetime
    verdict: str
    confidence: float          # 0.0 – 1.0
    confidence_pct: int        # round(confidence * 100)
    reasoning: str | None = None
    risk_signals: list[str] = []
    next_watch_items: list[str] = []
    reviewed_price: float | None = None


class ReviewTimelineResponse(BaseModel):
    thesis_id: int
    ticker: str
    title: str
    items: list[ReviewTimelineItem]   # newest first
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
    """One data-point on the Conviction Score Timeline.

    Sourced from ThesisSnapshot + nearest ThesisReview before that snapshot.
    breakdown may be None for legacy snapshots created before score_breakdown column.
    price may be None for review-triggered snapshots (price_at_snapshot not set).
    """

    snapshot_id: int
    snapshotted_at: datetime
    score: float                         # total 0–100
    score_tier: str                      # "Critical" | "Weak" | "Moderate" | "Healthy" | "Strong"
    score_tier_icon: str                 # "🔴" | "🟠" | "🟡" | "🟢" | "📎"
    breakdown: ConvictionBreakdown | None = None
    verdict: str | None = None           # ReviewVerdict from nearest prior AI review
    confidence: float | None = None      # AI confidence at nearest prior review
    price: float | None = None           # price_at_snapshot; None for review-triggered snapshots
    pnl_pct: float | None = None         # vs thesis entry_price


class ConvictionTrend(str):
    IMPROVING = "improving"
    DECLINING = "declining"
    STABLE = "stable"
    INSUFFICIENT_DATA = "insufficient_data"


class ConvictionTimelineResponse(BaseModel):
    """Conviction Score Timeline for a single thesis.

    points: oldest → newest (ascending snapshotted_at).
    trend: computed by comparing avg of first-3 vs last-3 data points.
           Δ > +5  → improving | Δ < -5  → declining | else → stable.
           < 2 data-points → insufficient_data.
    """

    thesis_id: int
    ticker: str
    title: str
    points: list[ConvictionPoint]         # oldest first
    trend: str                            # ConvictionTrend value
    latest_score: float | None            # score of newest point, None if no snapshots
    earliest_score: float | None          # score of oldest point, None if < 2 snapshots
    total: int                            # number of data-points returned


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------


class PortfolioPositionRow(BaseModel):
    """Một position trong portfolio — tương ứng với một thesis active.

    quantity=None: chỉ track thesis (P&L % có, P&L VND không có).
    quantity set:  full position view với cost_basis, market_value, pnl_abs.
    """

    thesis_id: int
    ticker: str
    title: str
    status: str

    # Position size
    quantity: float | None                # số CP nắm giữ
    entry_price: float | None             # giá vốn (VND)
    current_price: float | None           # giá hiện tại từ quote service

    # P&L
    pnl_pct: float | None                 # % so với entry_price
    pnl_abs: float | None                 # VND = (current - entry) * quantity
    cost_basis: float | None              # entry_price * quantity
    market_value: float | None            # current_price * quantity

    # Weight trong tổng portfolio (theo market_value, None nếu thiếu dữ liệu)
    weight_pct: float | None

    # AI context
    last_verdict: str | None
    score: float | None
    score_tier: str | None
    score_tier_icon: str | None

    # Price movement hôm nay
    change_pct: float | None = None       # % thay đổi so với hôm qua


class PortfolioSummary(BaseModel):
    """Tổng hợp portfolio của user.

    Bao gồm aggregate P&L + danh sách positions active.
    Positions được sắp xếp theo pnl_abs desc (người đang lãi nhất đứng đầu).

    has_quantity_data: True nếu ít nhất 1 position có quantity — cho biết
    UI có nên hiển thị cột VND hay chỉ hiển thị cột %.
    """

    user_id: str
    generated_at: datetime

    # Aggregate (chỉ có giá trị khi có ít nhất 1 position có đủ dữ liệu)
    total_cost_basis: float | None        # tổng vốn đầu tư (VND)
    total_market_value: float | None      # tổng giá trị thị trường (VND)
    total_pnl_abs: float | None           # lãi/lỗ tổng (VND)
    total_pnl_pct: float | None           # lãi/lỗ % bình quân gia quyền

    # Counts
    position_count: int                   # tổng số positions
    winning_count: int                    # pnl_pct > 0
    losing_count: int                     # pnl_pct < 0
    neutral_count: int                    # pnl_pct == 0 hoặc None

    # Flag cho UI
    has_quantity_data: bool               # True nếu ít nhất 1 position có quantity

    # Positions sorted by pnl_abs desc (None pnl_abs đẩy xuống cuối)
    positions: list[PortfolioPositionRow]
