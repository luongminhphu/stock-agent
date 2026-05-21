"""
Domain Event Catalog — Platform V2
All typed events emitted across segments.
Owner: platform segment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4


@dataclass(frozen=True)
class DomainEvent:
    """Base class for all domain events."""
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ─── watchlist / signal ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class SignalDetectedEvent(DomainEvent):
    """Emitted by watchlist.signal_engine when a tradeable signal is found."""
    symbol: str = ""
    signal_type: str = ""          # BREAKOUT | TREND_REVERSAL | THESIS_DIVERGENCE | ...
    strength: float = 0.0          # 0.0 – 1.0
    confidence: float = 0.0        # 0.0 – 1.0
    source: str = ""               # e.g. "technical" | "news" | "combined"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WatchlistScanCompletedEvent(DomainEvent):
    """Emitted after a full watchlist scan cycle finishes.

    user_id (added Wave 3): when provided, readmodel.CacheSubscriber
    invalidates only that user's scan_latest cache entry. When empty,
    the subscriber falls back to invalidating all scan_latest entries.
    Backward-compat: callers that omit user_id continue to work.
    """
    user_id: str = ""
    symbols_scanned: int = 0
    signals_found: int = 0
    duration_seconds: float = 0.0


# ─── portfolio / position ────────────────────────────────────────────────────

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


# ─── thesis ─────────────────────────────────────────────────────────────────────

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


@dataclass(frozen=True)
class StressTestCompletedEvent(DomainEvent):
    """Emitted by thesis.StressTestService after an adversarial stress-test run.

    Consumers:
      - watchlist.StressTestSubscriber: auto-creates ThesisTriggerAlert rules
        for broken/weakened assumptions surfaced by the AI.

    Owner: thesis segment (emitter), watchlist segment (subscriber).
    """
    thesis_id: str = ""
    user_id: str = ""
    symbol: str = ""
    thesis_title: str = ""
    verdict: str = ""                        # e.g. BULLISH | WEAKENING | BEARISH | INVALIDATED
    invalidation_probability: float = 0.0   # 0.0 – 1.0
    confidence: float = 0.0                 # 0.0 – 1.0
    suggested_triggers: list[str] = field(default_factory=list)
    broken_assumption_count: int = 0
    weakened_assumption_count: int = 0
    stress_scenario: str = ""


# ─── AI recommendations ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class RecommendationReadyEvent(DomainEvent):
    """Emitted when an AI agent produces a ProactiveRecommendation.

    Wave 7 fields (reasoning, action_detail, risk_signals, next_watch_items,
    thesis_id) carry rich content from ProactiveAlertOutput so that
    build_recommendation_embed() can render a detailed Discord embed without
    a secondary DB lookup.

    All Wave 7 fields default to empty so existing callers that only set the
    five core fields remain backward compatible.
    """
    symbol: str = ""
    action: str = ""               # BUY | SELL | REDUCE | HOLD | WATCH
    urgency: str = "MONITORING"    # NOW | TODAY | THIS_WEEK | MONITORING
    confidence: float = 0.0
    source_agent: str = ""         # proactive_alert | risk_assessment | opportunity_scout
    recommendation_id: str = field(default_factory=lambda: str(uuid4()))
    # Wave 7 rich-content fields
    reasoning: str = ""
    action_detail: str = ""
    risk_signals: tuple[str, ...] = field(default_factory=tuple)
    next_watch_items: tuple[str, ...] = field(default_factory=tuple)
    thesis_id: str = ""


@dataclass(frozen=True)
class SignalEngineRequestedEvent(DomainEvent):
    """Emitted by bot.SignalEngineScheduler to trigger the signal engine run.

    Consumed by: ai.SignalEngineListener
    Produced by: bot segment (scheduler adapter only — no business logic here)

    phase:
        morning — runs 08:40 ICT, 5 min before BriefingScheduler morning brief.
        eod     — runs 15:10 ICT, 5 min before DecisionReplayScheduler.
    """
    phase: str = "morning"          # morning | eod
    triggered_by: str = "scheduler"
    user_id: str = ""


@dataclass(frozen=True)
class SignalEngineCompletedEvent(DomainEvent):
    """Emitted by ai.SignalEngineListener after signal cross-check run completes.

    Consumed by: briefing.BriefingListener — injects summary into brief context
    so morning/eod brief carries real verdict instead of raw data dump.

    Produced by: ai segment (SignalEngineListener)

    summary: short narrative paragraph ready for BriefingService to embed.
    """
    phase: str = "morning"
    ranked_signal_count: int = 0
    thesis_review_trigger_count: int = 0
    risk_alert_count: int = 0
    opportunity_count: int = 0
    summary: str = ""               # AI-generated narrative for BriefingService


# ─── briefing ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BriefingRequestedEvent(DomainEvent):
    """Emitted by scheduler or push_trigger to request brief generation."""
    brief_type: str = "morning"    # morning | eod | alert
    triggered_by: str = "scheduler"
    context_hint: str = ""


@dataclass(frozen=True)
class BriefingReadyEvent(DomainEvent):
    """Emitted when a briefing document is ready to be delivered.

    user_id (added Wave 3): when provided, readmodel.CacheSubscriber
    invalidates only that user's brief_latest cache entry. When empty,
    the subscriber falls back to invalidating all brief_latest entries.
    Backward-compat: callers that omit user_id continue to work.
    """
    brief_type: str = ""
    channel: str = "discord"
    content_summary: str = ""
    user_id: str = ""


# ─── market ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MarketDataRefreshedEvent(DomainEvent):
    """Emitted after market quote batch is fetched and stored."""
    symbols_updated: int = 0
    source_adapter: str = ""       # vnstock | vndirect | tcbs
    trading_date: str = ""         # YYYY-MM-DD


# ─── opportunity screen ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class OpportunityScreenCompletedEvent(DomainEvent):
    """Emitted when the daily market-wide opportunity scan finishes."""
    candidates_found: int = 0
    top_symbol: str = ""
    screen_criteria: str = ""


# ─── trend shift ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TrendShiftEvent(DomainEvent):
    """Emitted by market.TrendShiftDetector when a portfolio symbol changes trend.

    Produced by: market segment (TrendShiftDetector)
    Consumed by: bot segment (TrendShiftSubscriber → Discord alert)

    shift_severity:
        MAJOR — both regime AND composite direction changed
                (e.g. TRENDING_UP → TRENDING_DOWN, composite 0.72 → 0.31)
        MINOR — one dimension changed with confidence >= 0.60
                (e.g. RANGING → TRENDING_DOWN, composite dropped below 0.4)

    composite_delta:
        Signed delta: current_composite − previous_composite.
        Negative = weakening, positive = strengthening.

    scan_phase:
        Which scheduled scan produced this event.
        Values: "morning" | "midday" | "pre_atc"
        Matches bot scheduler phase labels for easy log correlation.
    """
    symbol: str = ""
    previous_regime: str = ""      # TRENDING_UP | TRENDING_DOWN | RANGING | VOLATILE
    current_regime: str = ""
    previous_composite: float = 0.0
    current_composite: float = 0.0
    composite_delta: float = 0.0   # current − previous (negative = weakening)
    shift_severity: Literal["MAJOR", "MINOR"] = "MINOR"
    scan_phase: str = ""           # morning | midday | pre_atc
