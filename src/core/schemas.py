"""Core Intelligence Engine — Pydantic contracts.

Owner: core segment.
All downstream consumers (api, bot, briefing) import from here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.ai.schemas import IntelligenceReport


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
    market_phase: str = "unknown"


class PortfolioContext(BaseModel):
    total_positions: int = 0
    risk_breach_count: int = 0
    total_market_value: float | None = None
    top_exposed_tickers: list[str] = Field(default_factory=list)
    unrealized_pnl_pct: float | None = None


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
    watchlist: WatchlistContext = Field(default_factory=WatchlistContext)
    thesis: ThesisContext = Field(default_factory=ThesisContext)
    market: MarketContext = Field(default_factory=MarketContext)
    portfolio: PortfolioContext = Field(default_factory=PortfolioContext)

    watchlist_alerts: list[WatchlistAlert] = Field(default_factory=list)
    thesis_due_review: list[ThesisRef] = Field(default_factory=list)
    market_anomalies: list[MarketSignal] = Field(default_factory=list)
    portfolio_context: PortfolioContext = Field(default_factory=PortfolioContext)
    pending_briefings: list[str] = Field(default_factory=list)

    captured_at: datetime
    timestamp: datetime | None = None

    trigger_source: str = ""
    signal_engine_summary: str | None = None

    def model_post_init(self, __context: object) -> None:
        if self.timestamp is None:
            object.__setattr__(self, "timestamp", self.captured_at)


class RankedSignal(BaseModel):
    source: Literal["watchlist", "thesis", "market", "portfolio"]
    description: str
    urgency_score: float = Field(ge=0.0, le=1.0)
    raw_count: int | None = None


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
    intelligence_report: IntelligenceReport | None = None
    # Optional richer AI verdict — set by _EngineRunner when verdict_agent
    # succeeds. Listener should prefer this over .verdict when present.
    ai_verdict: Any | None = Field(default=None, exclude=True)


# ---------------------------------------------------------------------------
# FeedbackEntry — persisted by FeedbackStore, read by evolution.py
#
# Fields match EngineFeedbackSubmittedEvent exactly so feedback_listener.py
# can pass event fields through without remapping.
#
# Changelog:
#   - verdict_event_id replaces verdict_id (matches event field name)
#   - user_id, verdict, trigger_source added (were in listener call but missing here)
#   - outcome widened: adds values emitted by EngineFeedbackSubmittedEvent
#   - delta_score kept for evolution.py backward-compat (defaults to 0.0)
# ---------------------------------------------------------------------------

FeedbackOutcome = Literal[
    "correct",
    "incorrect",
    "partial",
    "not_acted",
    "acted",          # user took the recommended action
    "rejected",       # user explicitly dismissed the verdict
]


class FeedbackEntry(BaseModel):
    """Immutable value object representing one feedback record.

    Produced by FeedbackStore.record() and consumed by evolution.py.
    """
    verdict_event_id: str
    user_id: str = ""
    verdict: str = ""             # e.g. "BUY_SIGNAL", "HOLD"
    outcome: FeedbackOutcome = "not_acted"
    trigger_source: str = ""      # "bot", "api", "scheduler"
    user_note: str | None = None
    delta_score: float = 0.0      # reserved for evolution scoring
