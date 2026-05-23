"""
Core Intelligence Engine — Shared schemas.
Owner: core segment.
All output contracts consumed by briefing, bot, readmodel, feedback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass
class WatchlistContext:
    triggered_alert_count: int = 0
    top_tickers: list[str] = field(default_factory=list)
    has_volume_spike: bool = False


@dataclass
class ThesisContext:
    stale_count: int = 0            # thesis not reviewed in > 3 days
    drift_detected_count: int = 0   # thesis with price drift
    invalidated_count: int = 0


@dataclass
class MarketContext:
    trend_shift_count: int = 0      # symbols with recent regime change
    opportunity_count: int = 0
    market_phase: str = "unknown"   # pre_market | open | midday | close | post_market | closed


@dataclass
class PortfolioContext:
    risk_breach_count: int = 0
    total_positions: int = 0
    unrealized_pnl_pct: float | None = None


@dataclass
class SystemSnapshot:
    """Cross-segment state at a point in time — engine cycle input."""
    watchlist: WatchlistContext = field(default_factory=WatchlistContext)
    thesis: ThesisContext = field(default_factory=ThesisContext)
    market: MarketContext = field(default_factory=MarketContext)
    portfolio: PortfolioContext = field(default_factory=PortfolioContext)
    signal_engine_summary: str = ""   # injected from SignalEngineCompletedEvent if available
    captured_at: datetime = field(default_factory=datetime.now)
    trigger_source: str = ""


@dataclass
class RankedSignal:
    source: str              # "watchlist" | "thesis" | "market" | "portfolio"
    description: str
    urgency_score: float     # 0.0 → 1.0, computed by signals.py
    raw_count: int = 1


@dataclass
class EngineVerdict:
    """Structured output of one engine cycle — downstream segments consume."""
    verdict: Literal[
        "BUY_SIGNAL", "SELL_SIGNAL", "HOLD",
        "REVIEW_THESIS", "RISK_ALERT", "WATCH", "NO_ACTION"
    ]
    confidence: float                   # 0.0 → 1.0
    risk_signals: list[str]
    next_watch_items: list[str]
    action: str                         # human-readable, used by Discord / briefing
    reasoning_summary: str
    top_signals: list[RankedSignal]
    trigger_source: str
    generated_at: datetime = field(default_factory=datetime.now)
