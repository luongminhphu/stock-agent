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
class PortfolioSnapshotRequestedEvent(DomainEvent):
    """Emitted by bot.PortfolioSnapshotScheduler (08:15 ICT) to trigger snapshot build.

    Produced by: bot.PortfolioSnapshotScheduler
    Consumed by: portfolio.PortfolioSnapshotListener
    """
    user_id: str = ""
    phase: str = "morning"          # "morning" | "eod"
    triggered_by: str = "scheduler"


@dataclass(frozen=True)
class PortfolioSnapshotReadyEvent(DomainEvent):
    """Emitted by portfolio.PortfolioSnapshotListener after snapshot is built.

    Produced by:  portfolio.PortfolioSnapshotListener
    Consumed by:
      - core.IntelligenceEngineListener  → inject into SystemSnapshot
      - briefing.BriefingListener        → inject into morning brief context

    Backward-compatible: original 3 fields (total_positions, total_nav,
    unrealized_pnl) are preserved with same types and defaults.
    """
    user_id: str = ""
    total_positions: int = 0
    total_nav: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    top_exposed_tickers: tuple[str, ...] = field(default_factory=tuple)
    cash_pct: float = 0.0           # placeholder — cash model not yet modelled
    snapshot_phase: str = "morning"


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


# ─── opportunity screen ───────────────────────────────────────────────

@dataclass(frozen=True)
class OpportunityScreenCompletedEvent(DomainEvent):
    candidates_found: int = 0
    top_symbol: str = ""
    screen_criteria: str = ""
    # Serialised candidates for downstream AI handler.
    # Each item is a compact string from ScreenCandidate.format_for_prompt().
    # Defaults to empty tuple for backward compatibility.
    candidates_payload: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class OpportunityAIAnalysisRequestedEvent(DomainEvent):
    """Emitted by OpportunityScreenSubscriber to request AI cross-check.

    Produced by: market.OpportunityScreenSubscriber (Wave 3)
    Consumed by: ai.OpportunityAnalysisHandler

    Carries serialised candidates so the AI handler does not need to
    re-fetch market data — it only needs watchlist + thesis context.
    """
    user_id: str = ""
    candidates_payload: tuple[str, ...] = field(default_factory=tuple)
    screen_criteria: str = ""
    trading_date: str = ""
    top_symbol: str = ""


@dataclass(frozen=True)
class OpportunityAnalysisCompletedEvent(DomainEvent):
    """Emitted by ai.OpportunityAnalysisHandler after cross-check.

    Produced by: ai.OpportunityAnalysisHandler
    Consumed by: bot.OpportunityAnalysisSubscriber (Discord delivery)
    """
    user_id: str = ""
    verdict: str = ""           # e.g. "2 candidates overlap with watchlist"
    ranked_tickers: tuple[str, ...] = field(default_factory=tuple)
    watchlist_overlap: tuple[str, ...] = field(default_factory=tuple)
    thesis_relevant: tuple[str, ...] = field(default_factory=tuple)
    action: str = ""            # e.g. "REVIEW VHM and DGC before EOD"
    reasoning_summary: str = ""
    confidence: float = 0.0
    trading_date: str = ""


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

    All rich fields default to empty — backward-compatible with existing
    consumers that only read verdict / confidence / action_required / summary.

    user_id: investor user ID propagated from the originating run_cycle() call.
    Required by GlobalRiskSubscriber to scope store updates per-user.

    flagged_tickers: tickers extracted from the engine snapshot
    (watchlist_alerts + thesis_due_review + portfolio.top_exposed_tickers).
    Consumed by GlobalRiskStore, BriefingService, ScanService, ThesisMaintenanceService.

    agent_slots: execution record of each agent that ran during the multi-agent
    orchestration cycle (Wave C). Each slot carries agent_name, status
    ("ran" | "failed" | "skipped"), optional output dict, and ran_at timestamp.
    Defaults to empty tuple — consumers that only read verdict/confidence are
    unaffected. Consumed by Discord embed builder for per-agent status breakdown
    and by future readmodel intelligence_snapshot store.

    priority_actions: ordered list of recommended actions synthesised across
    all agent outputs. Each item is a dict with keys: action, ticker, urgency,
    reasoning. Defaults to empty tuple. Replaces the single `summary` string
    for downstream consumers that need structured action payloads.
    """
    user_id: str = ""
    verdict: str = "NO_ACTION"
    confidence: float = 0.0
    action_required: bool = False
    summary: str = ""                          # EngineVerdict.action
    trigger_source: str = ""
    verdict_event_id: str = field(default_factory=lambda: str(uuid4()))
    reasoning_summary: str = ""
    risk_signals: tuple[str, ...] = field(default_factory=tuple)
    next_watch_items: tuple[str, ...] = field(default_factory=tuple)
    sources: tuple[str, ...] = field(default_factory=tuple)
    # Ticker-level signals extracted from SystemSnapshot — used by readmodel store
    flagged_tickers: tuple[str, ...] = field(default_factory=tuple)
    # Multi-agent execution record (Wave C) — empty when heuristic fallback runs
    agent_slots: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    # Structured priority actions synthesised from all agent outputs
    priority_actions: tuple[dict[str, Any], ...] = field(default_factory=tuple)


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


# ─── user action feedback loop (Wave E) ──────────────────────────────────────

ActionType = Literal["SELL", "BUY", "IGNORE_ALERT", "MARK_REVIEWED", "DEFER"]


@dataclass(frozen=True)
class UserActionEvent(DomainEvent):
    """Emitted when the investor explicitly acts on a recommendation or alert.

    This is the primary feedback signal that closes the investor OS loop:

        watchlist → market → engine → briefing → user action
            ↑___________UserActionEvent___________________________↓

    Produced by:
      - bot: !sell, !buy, !ignore, !reviewed, !defer commands
      - api: POST /actions  (future REST surface)

    Consumed by:
      - core.UserActionFeedbackListener → side-effects per action_type:
          SELL         → thesis.mark_closed + watchlist.deprioritize
          BUY          → watchlist.ensure_tracked
          IGNORE_ALERT → watchlist.mute_alert
          MARK_REVIEWED→ thesis.touch_reviewed_at + readmodel invalidate
          DEFER        → watchlist.snooze
        All side-effects also fan out to memory.record_action for pattern learning.

    Fields:
        user_id:       investor identifier
        action_type:   one of ActionType literals
        ticker:        primary ticker the action applies to
        thesis_id:     optional — set when action is tied to a thesis
        alert_id:      optional — set when action is IGNORE_ALERT on a specific alert
        verdict_id:    optional — links back to the EngineVerdict that prompted action
        note:          free-text user comment (from bot command arguments)
        price:         execution price if known (SELL / BUY)
        mute_days:     for IGNORE_ALERT — how many days to suppress (default 7)
        snooze_hours:  for DEFER — how many hours to snooze watchlist (default 24)
    """
    user_id: str = ""
    action_type: ActionType = "DEFER"  # type: ignore[assignment]
    ticker: str = ""
    thesis_id: int | None = None
    alert_id: int | None = None
    verdict_id: str = ""
    note: str = ""
    price: float | None = None
    mute_days: int = 7
    snooze_hours: int = 24
