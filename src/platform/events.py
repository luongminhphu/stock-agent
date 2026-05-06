"""
Domain Event Catalog — Platform V2
All typed events emitted across segments.
Owner: platform segment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class DomainEvent:
    """Base class for all domain events."""
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=datetime.utcnow)


# ─── watchlist / signal ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class SignalDetectedEvent(DomainEvent):
    """Emitted by watchlist.signal_engine when a tradeable signal is found."""
    symbol: str = ""
    signal_type: str = ""          # BREAKOUT | TREND_REVERSAL | THESIS_DIVERGENCE | RISK_SPIKE | OPPORTUNITY_SCREEN
    strength: float = 0.0          # 0.0 – 1.0
    confidence: float = 0.0        # 0.0 – 1.0
    source: str = ""               # e.g. "technical" | "news" | "combined"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WatchlistScanCompletedEvent(DomainEvent):
    """Emitted after a full watchlist scan cycle finishes."""
    symbols_scanned: int = 0
    signals_found: int = 0
    duration_seconds: float = 0.0


# ─── portfolio / position ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class PositionRiskBreachedEvent(DomainEvent):
    """Emitted when a portfolio position hits a risk threshold."""
    symbol: str = ""
    breach_type: str = ""          # STOP_LOSS | CONCENTRATION | THESIS_INVALIDATED
    current_value: float = 0.0
    threshold_value: float = 0.0
    urgency: str = "TODAY"         # NOW | TODAY | THIS_WEEK


@dataclass(frozen=True)
class PortfolioSnapshotReadyEvent(DomainEvent):
    """Emitted after portfolio positions are refreshed from market data."""
    total_positions: int = 0
    total_nav: float = 0.0
    unrealized_pnl: float = 0.0


# ─── thesis ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ThesisInvalidatedEvent(DomainEvent):
    """Emitted when an active thesis hits an invalidation trigger."""
    thesis_id: str = ""
    symbol: str = ""
    trigger_description: str = ""
    invalidation_score: float = 0.0


@dataclass(frozen=True)
class ThesisReviewRequestedEvent(DomainEvent):
    """Emitted to trigger an AI thesis review (scheduled or signal-driven)."""
    thesis_id: str = ""
    symbol: str = ""
    reason: str = "scheduled"      # scheduled | signal | manual


# ─── AI recommendations ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class RecommendationReadyEvent(DomainEvent):
    """Emitted when an AI agent produces a ProactiveRecommendation.

    Rich fields (reasoning, action_detail, risk_signals, next_watch_items,
    thesis_id) are optional — all default to empty so existing consumers
    remain backward-compatible.
    """
    symbol: str = ""
    action: str = ""               # BUY | SELL | REDUCE | HOLD | WATCH
    urgency: str = "MONITORING"    # NOW | TODAY | THIS_WEEK | MONITORING
    confidence: float = 0.0
    source_agent: str = ""         # proactive_alert | risk_assessment | opportunity_scout
    recommendation_id: str = field(default_factory=lambda: str(uuid4()))
    # ── rich content fields (Wave 7) ──────────────────────────────────────────
    reasoning: str = ""            # Short AI reasoning (1-3 sentences)
    action_detail: str = ""        # Specific action text, e.g. "Mua breakout trên 93,500"
    risk_signals: list[str] = field(default_factory=list)    # Up to 5 risk bullets
    next_watch_items: list[str] = field(default_factory=list) # Up to 3 follow-up items
    thesis_id: str = ""            # Non-empty when recommendation is thesis-linked


# ─── briefing ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BriefingRequestedEvent(DomainEvent):
    """Emitted by scheduler or push_trigger to request brief generation."""
    brief_type: str = "morning"    # morning | eod | alert
    triggered_by: str = "scheduler"
    context_hint: str = ""


@dataclass(frozen=True)
class BriefingReadyEvent(DomainEvent):
    """Emitted when a briefing document is ready to be delivered."""
    brief_type: str = ""
    channel: str = "discord"
    content_summary: str = ""


# ─── market ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MarketDataRefreshedEvent(DomainEvent):
    """Emitted after market quote batch is fetched and stored."""
    symbols_updated: int = 0
    source_adapter: str = ""       # vnstock | vndirect | tcbs
    trading_date: str = ""         # YYYY-MM-DD


# ─── opportunity screen ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class OpportunityScreenCompletedEvent(DomainEvent):
    """Emitted when the daily market-wide opportunity scan finishes."""
    candidates_found: int = 0
    top_symbol: str = ""
    screen_criteria: str = ""
