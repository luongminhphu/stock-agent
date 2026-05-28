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


# ─── watchlist / signal ───────────────────────────────────────────────

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
    user_id: str = ""
    symbols_scanned: int = 0
    signals_found: int = 0
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class ProactiveWatchRequestedEvent(DomainEvent):
    user_id: str = ""
    phase: str = "morning"
    triggered_by: str = "scheduler"


@dataclass(frozen=True)
class ProactiveWatchAlertFiredEvent(DomainEvent):
    user_id: str = ""
    alert_id: int = 0
    ticker: str = ""
    condition_type: str = ""
    threshold: float = 0.0
    triggered_price: float | None = None
    note: str = ""
    label: str = ""
    priority: str | None = None
    phase: str = "morning"
    scan_event_id: str = ""


# ─── portfolio / position ────────────────────────────────────────────

@dataclass(frozen=True)
class PositionRiskBreachedEvent(DomainEvent):
    symbol: str = ""
    breach_type: str = ""
    current_value: float = 0.0
    threshold_value: float = 0.0
    urgency: str = "TODAY"


@dataclass(frozen=True)
class PortfolioSnapshotReadyEvent(DomainEvent):
    total_positions: int = 0
    total_nav: float = 0.0
    unrealized_pnl: float = 0.0


# ─── thesis ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ThesisInvalidatedEvent(DomainEvent):
    thesis_id: str = ""
    symbol: str = ""
    trigger_description: str = ""
    invalidation_score: float = 0.0


@dataclass(frozen=True)
class ThesisReviewRequestedEvent(DomainEvent):
    thesis_id: str = ""
    symbol: str = ""
    reason: str = "scheduled"


@dataclass(frozen=True)
class ThesisReviewTriggeredEvent(DomainEvent):
    """Emitted per ThesisReviewTrigger from SignalEngineAgent output.

    Produced by: ai.SignalEngineListener (after agent run completes)
    Consumed by: thesis.SignalReviewTriggerListener
                 → loads thesis from DB → enqueues ThesisJudgeAgent

    thesis_id may be empty string in fallback mode (AI unavailable).
    In that case, listener resolves thesis by ticker from active theses.

    urgency: "CRITICAL" | "HIGH" — matches ThesisReviewTrigger.urgency.
    phase:   propagated from the originating SignalEngineRequestedEvent.
    """
    thesis_id: str = ""
    ticker: str = ""
    reason: str = ""
    urgency: str = "HIGH"
    phase: str = "morning"
    user_id: str = ""


@dataclass(frozen=True)
class ThesisClosedEvent(DomainEvent):
    thesis_id: int = 0
    user_id: str = ""
    ticker: str = ""
    close_reason: str = "closed"
    thesis_title: str = ""
    thesis_summary: str = ""
    outcome_pnl_pct: float | None = None


@dataclass(frozen=True)
class ThesisPostMortemReadyEvent(DomainEvent):
    thesis_id: int = 0
    user_id: str = ""
    ticker: str = ""
    close_reason: str = "closed"
    thesis_title: str = ""
    lesson: str = ""
    pattern: str = ""
    verdict: str = "INCONCLUSIVE"
    confidence: float = 0.0
    outcome_pnl_pct: float | None = None
    memory_tags: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class StressTestCompletedEvent(DomainEvent):
    thesis_id: str = ""
    user_id: str = ""
    symbol: str = ""
    thesis_title: str = ""
    verdict: str = ""
    invalidation_probability: float = 0.0
    confidence: float = 0.0
    suggested_triggers: list[str] = field(default_factory=list)
    broken_assumption_count: int = 0
    weakened_assumption_count: int = 0
    stress_scenario: str = ""


# ─── AI recommendations ───────────────────────────────────────────────

@dataclass(frozen=True)
class RecommendationReadyEvent(DomainEvent):
    symbol: str = ""
    action: str = ""
    urgency: str = "MONITORING"
    confidence: float = 0.0
    source_agent: str = ""
    recommendation_id: str = field(default_factory=lambda: str(uuid4()))
    reasoning: str = ""
    action_detail: str = ""
    risk_signals: tuple[str, ...] = field(default_factory=tuple)
    next_watch_items: tuple[str, ...] = field(default_factory=tuple)
    thesis_id: str = ""


@dataclass(frozen=True)
class SignalEngineRequestedEvent(DomainEvent):
    phase: str = "morning"
    triggered_by: str = "scheduler"
    user_id: str = ""


@dataclass(frozen=True)
class SignalEngineCompletedEvent(DomainEvent):
    """Emitted after SignalEngineAgent finishes a run.

    triggers: structured ThesisReviewTrigger payloads forwarded from
    SignalEngineOutput.thesis_review_triggers. Each item is a dict with
    keys: thesis_id, ticker, reason, urgency. Defaults to empty tuple
    for backward compatibility with existing consumers that only read
    the count fields.
    """
    phase: str = "morning"
    ranked_signal_count: int = 0
    thesis_review_trigger_count: int = 0
    risk_alert_count: int = 0
    opportunity_count: int = 0
    summary: str = ""
    triggers: tuple[dict[str, str], ...] = field(default_factory=tuple)


# ─── briefing ────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BriefingRequestedEvent(DomainEvent):
    brief_type: str = "morning"
    triggered_by: str = "scheduler"
    context_hint: str = ""


@dataclass(frozen=True)
class BriefingReadyEvent(DomainEvent):
    brief_type: str = ""
    channel: str = "discord"
    content_summary: str = ""
    user_id: str = ""


# ─── market ─────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MarketDataRefreshedEvent(DomainEvent):
    symbols_updated: int = 0
    source_adapter: str = ""
    trading_date: str = ""


# ─── opportunity screen ───────────────────────────────────────────────

@dataclass(frozen=True)
class OpportunityScreenCompletedEvent(DomainEvent):
    candidates_found: int = 0
    top_symbol: str = ""
    screen_criteria: str = ""


# ─── trend shift ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TrendShiftEvent(DomainEvent):
    symbol: str = ""
    previous_regime: str = ""
    current_regime: str = ""
    previous_composite: float = 0.0
    current_composite: float = 0.0
    composite_delta: float = 0.0
    shift_severity: Literal["MAJOR", "MINOR"] = "MINOR"
    scan_phase: str = ""


# ─── trend prediction ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TrendPredictionCompletedEvent(DomainEvent):
    scan_phase: str = "morning"
    symbols_analyzed: int = 0
    top_verdicts: tuple[tuple[str, str], ...] = field(default_factory=tuple)


# ─── core intelligence engine ──────────────────────────────────────────────

@dataclass(frozen=True)
class IntelligenceEngineRequestedEvent(DomainEvent):
    trigger_type: str = "scheduled"
    trigger_source: str = ""
    user_id: str = ""
    priority: str = "normal"
    context_hint: str | None = None
    signal_engine_summary: str = ""


@dataclass(frozen=True)
class IntelligenceEngineCompletedEvent(DomainEvent):
    """Emitted by IntelligenceEngineListener after a successful engine cycle.

    Carries the full verdict payload needed by both:
      - downstream event bus consumers (EngineFeedbackListener, GlobalRiskSubscriber, future subscribers)
      - Discord embed builder (build_engine_verdict_embed)

    All rich fields (risk_signals, next_watch_items, reasoning_summary, sources)
    default to empty — backward-compatible with existing consumers that only
    read verdict / confidence / action_required / summary.

    verdict_event_id: echoed from EngineVerdict.verdict_event_id so
    EngineFeedbackSubmittedEvent can cross-reference the originating verdict.

    user_id: investor user ID propagated from the originating run_cycle() call.
    Required by GlobalRiskSubscriber to scope store updates per-user.
    """
    user_id: str = ""
    verdict: str = "NO_ACTION"
    confidence: float = 0.0
    action_required: bool = False
    summary: str = ""                          # EngineVerdict.action
    trigger_source: str = ""
    verdict_event_id: str = field(default_factory=lambda: str(uuid4()))
    # Rich verdict fields — populated from EngineVerdict by IntelligenceEngineListener
    reasoning_summary: str = ""               # EngineVerdict.reasoning_summary
    risk_signals: tuple[str, ...] = field(default_factory=tuple)
    next_watch_items: tuple[str, ...] = field(default_factory=tuple)
    sources: tuple[str, ...] = field(default_factory=tuple)


# ─── core intelligence feedback ───────────────────────────────────────────

@dataclass(frozen=True)
class EngineFeedbackSubmittedEvent(DomainEvent):
    verdict_event_id: str = ""
    user_id: str = ""
    verdict: str = ""
    outcome: str = "not_acted"
    trigger_source: str = ""
    user_note: str = ""


# ─── core self-improvement (Wave 4) ────────────────────────────────────────

@dataclass(frozen=True)
class EvolutionSuggestionReadyEvent(DomainEvent):
    """Emitted after SelfImprovementAdvisor completes a run.

    Produced by: core.evolution_scheduler (bot scheduled job, weekly)
    Consumed by: bot.EvolutionSubscriber → Discord embed for owner review

    suggestion_count:
        Number of ImprovementSuggestion rows saved this run.
        0 = no patterns found (system is performing well).

    overall_accuracy:
        Rounded float from PatternReport.overall_accuracy.
        Helps owner gauge health at a glance without opening DB.

    run_id:
        UUID linking this event to all evolution_log rows from this run.
        Use with EvolutionStore.get_history() to fetch full suggestion list.

    has_high_risk:
        True if any suggestion in this run has risk_level='high'.
        Bot subscriber uses this to elevate notification priority.
    """
    run_id: str = ""
    suggestion_count: int = 0
    overall_accuracy: float = 0.0
    has_high_risk: bool = False
    period_days: int = 30


# ─── daily agenda ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DailyAgendaCompletedEvent(DomainEvent):
    """Emitted by AgendaScheduler after DailyAgendaResult is built and persisted.

    Produced by: briefing.AgendaScheduler.run_for_user() (07:30 ICT)
    Consumed by:
      - bot.AgendaNotifier (optional) → Discord embed "Today: 2 decide / 3 watch"
      - briefing.BriefingService._build_agenda_context() → inject into morning brief

    decide_tickers / watch_tickers: top-level ticker lists (max 10 each) for
    quick fanout without loading full DailyAgendaResult from DB.
    opening_line: AI-generated 1-sentence summary for Discord preview.
    """
    user_id: str = ""
    decide_count: int = 0
    watch_count: int = 0
    defer_count: int = 0
    decide_tickers: tuple[str, ...] = field(default_factory=tuple)
    watch_tickers: tuple[str, ...] = field(default_factory=tuple)
    opening_line: str = ""
