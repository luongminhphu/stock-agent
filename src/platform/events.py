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
    signal_type: str = ""  # BREAKOUT | TREND_REVERSAL | THESIS_DIVERGENCE | ...
    strength: float = 0.0  # 0.0 – 1.0
    confidence: float = 0.0  # 0.0 – 1.0
    source: str = ""  # e.g. "technical" | "news" | "combined"
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
    phase: str = "morning"  # "morning" | "eod"
    triggered_by: str = "scheduler"


@dataclass(frozen=True)
class PortfolioSnapshotReadyEvent(DomainEvent):
    """Emitted by portfolio.PortfolioSnapshotListener after snapshot is built.

    Produced by:  portfolio.PortfolioSnapshotListener
    Consumed by:
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
    cash_pct: float = 0.0  # placeholder — cash model not yet modelled
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


# ─── AI recommendations ───────────────────────────────────────────────


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


@dataclass(frozen=True)
class BriefFeedbackRecordedEvent(DomainEvent):
    """Emitted by briefing.BriefingService.record_feedback sau khi lưu BriefFeedback.

    Consumed by: ai.memory.FeedbackLedgerSubscriber → user_behavior_logs
    (source="briefing", signal="brief:<outcome>", ref_type="brief").
    """

    brief_snapshot_id: int = 0
    user_id: str = ""
    outcome: str = ""  # acted | watching | skipped
    brief_type: str = ""  # morning | eod | ""


@dataclass(frozen=True)
class PretradeAdviceReconciledEvent(DomainEvent):
    """Emitted by thesis.DecisionService.reconcile_pretrade_with_action (Wave E3b).

    Khi user BUY/SELL thật trong cửa sổ sau một PRETRADE_ADVICE, thesis phân loại
    adherence: followed_advice | ignored_advice. Consumed by ai.memory ledger.
    """

    user_id: str = ""
    ticker: str = ""
    decision_log_id: int = 0
    advice_verdict: str = ""  # BULLISH | BEARISH | NEUTRAL
    action_type: str = ""  # BUY | SELL
    adherence: str = ""  # followed_advice | ignored_advice


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
    action_type: ActionType = "DEFER"
    ticker: str = ""
    thesis_id: int | None = None
    alert_id: int | None = None
    verdict_id: str = ""
    note: str = ""
    price: float | None = None
    mute_days: int = 7
    snooze_hours: int = 24
