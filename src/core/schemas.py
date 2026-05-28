"""Core Intelligence Engine — Pydantic contracts.

Owner: core segment.
All downstream consumers (api, bot, briefing) import from here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# SystemSnapshot sub-models (nested, used by signals.py)
# ---------------------------------------------------------------------------


class WatchlistContext(BaseModel):
    triggered_alert_count: int = 0
    top_tickers: list[str] = Field(default_factory=list)
    has_volume_spike: bool = False


class ThesisContext(BaseModel):
    invalidated_count: int = 0
    drift_detected_count: int = 0
    stale_count: int = 0
    stale_tickers: list[str] = Field(default_factory=list)


class MarketContext(BaseModel):
    trend_shift_count: int = 0
    opportunity_count: int = 0
    top_opportunity_tickers: list[str] = Field(default_factory=list)
    market_phase: str = "unknown"  # e.g. "morning", "midday", "closing", "after-hours"


class PortfolioContext(BaseModel):
    total_positions: int = 0
    risk_breach_count: int = 0
    total_market_value: float | None = None
    top_exposed_tickers: list[str] = Field(default_factory=list)
    unrealized_pnl_pct: float | None = None  # aggregate unrealized PnL %


# ---------------------------------------------------------------------------
# SystemSnapshot — cross-segment state at a point in time
# ---------------------------------------------------------------------------


# Flat compat aliases (used by engine.py Wave 1 legacy paths)
class WatchlistAlert(BaseModel):
    ticker: str
    alert_type: str
    triggered_at: datetime
    note: str | None = None


class ThesisRef(BaseModel):
    thesis_id: int
    ticker: str
    last_reviewed_at: datetime | None = None
    days_overdue: int = 0


class MarketSignal(BaseModel):
    ticker: str
    signal_type: str
    value: float | None = None
    note: str | None = None


class SystemSnapshot(BaseModel):
    # Nested sub-models (primary, consumed by signals.py)
    watchlist: WatchlistContext = Field(default_factory=WatchlistContext)
    thesis: ThesisContext = Field(default_factory=ThesisContext)
    market: MarketContext = Field(default_factory=MarketContext)
    portfolio: PortfolioContext = Field(default_factory=PortfolioContext)

    # Flat legacy lists (consumed by engine.py _derive_action, briefing)
    watchlist_alerts: list[WatchlistAlert] = Field(default_factory=list)
    thesis_due_review: list[ThesisRef] = Field(default_factory=list)
    market_anomalies: list[MarketSignal] = Field(default_factory=list)
    portfolio_context: PortfolioContext = Field(default_factory=PortfolioContext)
    pending_briefings: list[str] = Field(default_factory=list)

    # Primary timestamp (replaces legacy `timestamp` as the canonical field)
    captured_at: datetime
    # Legacy alias — kept for backward compat with any code reading .timestamp
    timestamp: datetime | None = None

    # Context forwarded from scheduler / event caller
    trigger_source: str = ""   # e.g. "scheduler", "discord_command", "manual"
    signal_engine_summary: str | None = None  # free-text summary from upstream

    def model_post_init(self, __context: object) -> None:  # noqa: ANN001
        """Keep timestamp in sync with captured_at for backward compat."""
        if self.timestamp is None:
            object.__setattr__(self, "timestamp", self.captured_at)


# ---------------------------------------------------------------------------
# RankedSignal — output of signals.rank_signals()
# ---------------------------------------------------------------------------


class RankedSignal(BaseModel):
    source: Literal["watchlist", "thesis", "market", "portfolio"]
    description: str
    urgency_score: float = Field(ge=0.0, le=1.0)
    raw_count: int | None = None


# ---------------------------------------------------------------------------
# EngineVerdict — structured output of engine synthesis
# ---------------------------------------------------------------------------

VerdictType = Literal[
    "BUY_SIGNAL",
    "SELL_SIGNAL",
    "HOLD",
    "REVIEW_THESIS",
    "RISK_ALERT",
    "NO_ACTION",
]


class EngineVerdict(BaseModel):
    verdict_id: str
    verdict: VerdictType
    confidence: float = Field(ge=0.0, le=1.0)
    risk_signals: list[str] = Field(default_factory=list)
    next_watch_items: list[str] = Field(default_factory=list)
    action: str
    reasoning_summary: str
    sources: list[str] = Field(default_factory=list)
    generated_at: datetime


class EngineOutput(BaseModel):
    snapshot: SystemSnapshot
    verdict: EngineVerdict
    dispatched_to: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# FeedbackEntry — self-improvement loop input
# ---------------------------------------------------------------------------


class FeedbackEntry(BaseModel):
    verdict_id: str
    outcome: Literal["correct", "incorrect", "partial", "not_acted"]
    user_note: str | None = None
    delta_score: float = 0.0  # used by evolution.py to reweight signal weights
