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


@dataclass(frozen=True)
class ProactiveWatchRequestedEvent(DomainEvent):
    """Emitted by bot.ProactiveWatchScheduler to trigger a proactive alert scan.

    Produced by: bot segment (scheduler — timing adapter only, no logic)
    Consumed by: watchlist.ProactiveWatchListener

    phase:
        morning  — 09:15 ICT  (just after market open)
        midday   — 11:15 ICT  (mid-session check)
        pre_atc  — 14:15 ICT  (30 min before ATC)
    """
    user_id: str = ""
    phase: str = "morning"          # morning | midday | pre_atc
    triggered_by: str = "scheduler"


@dataclass(frozen=True)
class ProactiveWatchAlertFiredEvent(DomainEvent):
    """Emitted by watchlist.ProactiveWatchListener for each alert that fires.

    Produced by: watchlist segment (ProactiveWatchListener)
    Consumed by: bot segment (ProactiveWatchSubscriber → Discord notify)

    One event per fired alert — downstream subscriber batches or sends individually.

    condition_type: mirrors AlertConditionType.value string
        e.g. "PRICE_ABOVE" | "PRICE_BELOW" | "CHANGE_PCT_UP" | "CHANGE_PCT_DOWN"
             | "VOLUME_SPIKE" | "THESIS_TRIGGER"

    priority: from Alert.priority field
        "HIGH" | "MEDIUM" | "LOW" | None (standard alerts have no priority)
    """
    user_id: str = ""
    alert_id: int = 0
    ticker: str = ""
    condition_type: str = ""
    threshold: float = 0.0
    triggered_price: float | None = None
    note: str = ""
    label: str = ""
    priority: str | None = None
    phase: str = "morning"          # echoes ProactiveWatchRequestedEvent.phase
    scan_event_id: str = ""         # event_id of originating ProactiveWatchRequestedEvent


# ─── portfolio / position ────────────────────────────────────────────

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


# ─── thesis ──────────────────────────────────────────────────────────────────────

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
class ThesisClosedEvent(DomainEvent):
    """Emitted by ThesisService when a thesis is closed or invalidated.

    Produced by: thesis segment (ThesisService.close / ThesisService.invalidate)
    Consumed by: thesis.PostMortemService → triggers AI lesson extraction

    close_reason:
        closed      — investor manually closed the thesis (target reached, position exited)
        invalidated — thesis hit an invalidation trigger

    outcome_pnl_pct:
        Final realised P&L percentage, if available from DecisionLog.
        None when P&L data is not yet available (post-mortem still runs,
        AI will note the absence).

    thesis_summary:
        Snapshot of the thesis title + summary at close time.
        Avoids a second DB lookup inside PostMortemService.
    """
    thesis_id: int = 0
    user_id: str = ""
    ticker: str = ""
    close_reason: str = "closed"    # closed | invalidated
    thesis_title: str = ""
    thesis_summary: str = ""
    outcome_pnl_pct: float | None = None


@dataclass(frozen=True)
class ThesisPostMortemReadyEvent(DomainEvent):
    """Emitted by thesis.PostMortemService after AI lesson extraction completes.

    Produced by: thesis segment (PostMortemService)
    Consumed by:
      - ai.MemoryInjectionListener  → write structured memory entry
      - bot.PostMortemSubscriber    → Discord embed in decision channel

    lesson:
        Core lesson extracted by AI — what went right/wrong with this thesis.

    pattern:
        Short pattern label for indexing (e.g. "premature_entry", "thesis_drift",
        "catalyst_miss", "correct_breakout"). Empty string if AI could not classify.

    verdict:
        AI verdict on outcome quality.
        Values: CORRECT | INCORRECT | MIXED | INCONCLUSIVE

    memory_tags:
        Tuple of keyword tags for memory store indexing.
        e.g. ("breakout", "VCB", "catalyst_miss")
    """
    thesis_id: int = 0
    user_id: str = ""
    ticker: str = ""
    close_reason: str = "closed"
    thesis_title: str = ""
    lesson: str = ""
    pattern: str = ""
    verdict: str = "INCONCLUSIVE"   # CORRECT | INCORRECT | MIXED | INCONCLUSIVE
    confidence: float = 0.0
    outcome_pnl_pct: float | None = None
    memory_tags: tuple[str, ...] = field(default_factory=tuple)


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


# ─── AI recommendations ──────────────────────────────────────────────

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


# ─── briefing ────────────────────────────────────────────────────────────────────────

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


# ─── market ─────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MarketDataRefreshedEvent(DomainEvent):
    """Emitted after market quote batch is fetched and stored."""
    symbols_updated: int = 0
    source_adapter: str = ""       # vnstock | vndirect | tcbs
    trading_date: str = ""         # YYYY-MM-DD


# ─── opportunity screen ───────────────────────────────────────────────

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


# ─── trend prediction ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TrendPredictionCompletedEvent(DomainEvent):
    """Emitted by ai.TrendEngineListener after trend prediction run completes.

    Produced by: ai segment (TrendEngineListener)
    Consumed by:
      - briefing.BriefingListener: inject top verdicts into morning/eod brief.
      - bot segment: optional Discord push for actionable verdicts.

    top_verdicts:
        Tuple of (symbol, verdict) pairs for the top-3 most confident
        predictions, sorted by confidence descending. Empty tuple on
        failure or when no symbols are in watchlist.

    scan_phase:
        Mirrors SignalEngineRequestedEvent.phase so consumers can correlate
        the prediction run with the triggering scan cycle.
    """
    scan_phase: str = "morning"             # morning | eod
    symbols_analyzed: int = 0
    top_verdicts: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    # top_verdicts: (("VHM", "BUY"), ("FPT", "HOLD"), ("TCB", "WATCH"))


# ─── core intelligence engine ──────────────────────────────────────────────

@dataclass(frozen=True)
class IntelligenceEngineRequestedEvent(DomainEvent):
    """Trigger an Intelligence Engine cycle.

    Produced by: bot.IntelligenceEngineScheduler | api command | any segment emitter
    Consumed by: core.IntelligenceEngineListener

    trigger_type:
        scheduled    — from bot scheduler (pre_market, midday, eod)
        event_driven — triggered by another event (e.g. multi-signal convergence)
        manual       — user-initiated via Discord command or API

    priority:
        low    — defer if system is busy
        normal — standard cycle
        high   — bypass confidence threshold, always dispatch verdict

    signal_engine_summary:
        Optional narrative from a prior SignalEngineCompletedEvent.summary.
        When provided, injected into SystemSnapshot and the AI verdict prompt
        for richer cross-segment context. Empty string = not available.
    """
    trigger_type: str = "scheduled"   # scheduled | event_driven | manual
    trigger_source: str = ""           # e.g. "scheduler:pre_market" | "user:discord"
    user_id: str = ""
    priority: str = "normal"           # low | normal | high
    context_hint: str | None = None    # optional freeform hint injected into reasoning
    signal_engine_summary: str = ""    # from SignalEngineCompletedEvent.summary if available


@dataclass(frozen=True)
class IntelligenceEngineCompletedEvent(DomainEvent):
    """Emitted after an Intelligence Engine cycle completes.

    Produced by: core.IntelligenceEngineListener
    Consumed by:
      - briefing.BriefingListener  → inject verdict context into next brief
      - bot.EngineSubscriber       → Discord embed when action_required=True

    verdict:
        BUY_SIGNAL | SELL_SIGNAL | HOLD | REVIEW_THESIS | RISK_ALERT | WATCH | NO_ACTION

    action_required:
        True when verdict is actionable (not NO_ACTION or HOLD).
        Downstream subscribers use this to decide whether to notify.

    summary:
        Human-readable action string, ready for Discord or briefing injection.

    verdict_event_id:
        Echoes the event_id of this IntelligenceEngineCompletedEvent so that
        downstream feedback submissions can reference it in
        EngineFeedbackSubmittedEvent.verdict_event_id.
    """
    verdict: str = "NO_ACTION"
    confidence: float = 0.0
    action_required: bool = False
    summary: str = ""
    trigger_source: str = ""
    verdict_event_id: str = field(default_factory=lambda: str(uuid4()))


# ─── core intelligence feedback ───────────────────────────────────────────

@dataclass(frozen=True)
class EngineFeedbackSubmittedEvent(DomainEvent):
    """Emitted when a user submits outcome feedback on an EngineVerdict.

    Produced by: bot.FeedbackCommandHandler | api.feedback_router
    Consumed by: core.EngineFeedbackListener → FeedbackStore.record()

    verdict_event_id:
        The event_id of the IntelligenceEngineCompletedEvent being rated.
        Used to link feedback to the original verdict row.

    verdict:
        The verdict string echoed from the original completed event
        (BUY_SIGNAL | SELL_SIGNAL | HOLD | REVIEW_THESIS | RISK_ALERT | WATCH | NO_ACTION).
        Stored denormalised so feedback queries don’t need a join.

    outcome:
        correct    — verdict proved right, user acted and it worked
        incorrect  — verdict was wrong or misleading
        partial    — partly correct (e.g. right direction, wrong timing)
        not_acted  — user saw it but didn’t act (neutral signal)

    user_note:
        Optional free-text note from the user explaining the outcome.
        Stored in DB and surfaced to Wave 4 evolution analysis.
    """
    verdict_event_id: str = ""
    user_id: str = ""
    verdict: str = ""
    outcome: str = "not_acted"     # correct | incorrect | partial | not_acted
    trigger_source: str = ""
    user_note: str = ""
