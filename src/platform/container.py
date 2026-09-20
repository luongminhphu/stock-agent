"""AppContainer — nơi duy nhất giữ singleton runtime (Wave E2b).

Owner: platform segment.

Thay cho ~47 module-global trong ``bootstrap.py``: một dataclass có tên trường rõ,
nhóm theo segment sở hữu. ``bootstrap()`` là writer duy nhất; mọi consumer đọc qua
``bootstrap.get_*()`` (giữ nguyên contract) hoặc ``container.require("...")``.

Quy ước:
- Trường mặc định ``None`` = chưa wire. ``require`` raise RuntimeError với thông điệp
  cũ "bootstrap() has not been called" để tương thích caller hiện tại.
- Kiểu để ``object`` có chủ ý: platform không import segment (import-linter contract
  ``platform-independent``); typing chặt hơn thuộc wave sau qua Protocol.
- ``reset()`` chỉ dùng trong tests.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any


@dataclass
class AppContainer:
    # ── platform ──
    session_factory: object | None = None  # AsyncSessionLocal — cache để getter không re-import
    # ── market ──
    quote_service: object | None = None
    ohlcv_service: object | None = None
    market_regime_service: object | None = None  # Wave 8.1: pretrade market-regime gate
    ticker_context_service: object | None = None  # Wave B2: market context đã tinh chế
    snapshot_scheduler: object | None = None
    opportunity_screen_scheduler: object | None = None  # Wave 3
    proactive_discovery_service: object | None = None  # Proactive Discovery: portfolio-aware picks
    # ── ai ──
    ai_client: object | None = None
    thesis_review_agent: object | None = None
    thesis_debate_agent: object | None = None
    thesis_suggest_agent: object | None = None
    briefing_agent: object | None = None
    why_agent: object | None = None
    pretrade_agent: object | None = None
    stress_test_agent: object | None = None
    replay_agent: object | None = None
    sector_rotation_agent: object | None = None
    memory_consolidator: object | None = None
    proactive_alert_agent: object | None = None
    opportunity_screen_subscriber: object | None = None  # Wave 3
    opportunity_analysis_handler: object | None = None  # Wave 3: AI cross-check handler
    signal_engine_agent: object | None = None  # Wave 2b: cross-check engine
    signal_engine_listener: object | None = None  # Wave B2: fully wired
    agenda_builder_agent: object | None = None  # AgendaBuilderAgent singleton
    trend_reasoning_agent: object | None = None  # TrendReasoningAgent singleton
    trend_engine_listener: object | None = None  # TrendEngineListener singleton
    memory_injection_listener: object | None = None  # Wave E: MemoryInjectionListener singleton
    # ── core ──
    intelligence_engine_listener: object | None = None  # core: IntelligenceEngineListener
    engine_feedback_listener: object | None = None  # core: FeedbackStore bridge
    user_action_listener: object | None = None  # core: UserActionFeedbackListener (feedback loop)
    feedback_ledger_subscriber: object | None = None  # ai.memory: FeedbackLedgerSubscriber (E3a)
    # ── thesis ──
    investor_profile_service: tuple[Any, ...] | None = None
    thesis_review_listener: object | None = None
    signal_review_trigger_listener: object | None = None  # Wave C: SignalEngine → ThesisReview
    post_mortem_service: object | None = None  # Wave E: PostMortemService singleton
    # ── watchlist ──
    stress_test_subscriber: object | None = None  # G4: StressTest → Watchlist bridge
    proactive_watch_listener: object | None = None  # watchlist: ProactiveWatchListener singleton
    # ── briefing ──
    briefing_listener: object | None = None
    agenda_service_factory: object | None = None  # callable(session) -> AgendaService | None
    # ── portfolio ──
    portfolio_snapshot_listener: object | None = None  # PortfolioSnapshotListener singleton
    pnl_service_class: type | None = None  # PnlService class (portfolio)
    # ── readmodel (projection/cache; ORM đã về owner ở E1) ──
    trend_prediction_store: object | None = None  # TrendPredictionStore singleton
    recent_reviews_store: object | None = None  # W1: RecentReviewsStore readmodel singleton
    portfolio_query_adapter: object | None = None  # W3: PortfolioQueryAdapter singleton
    global_risk_subscriber: object | None = None  # readmodel: GlobalRiskSubscriber singleton
    intelligence_snapshot_subscriber: object | None = None  # IntelligenceSnapshotSubscriber
    trend_snapshot_store: object | None = None  # Wave D.1: persisted TrendSnapshotStore
    # ── bot ──
    intelligence_engine_subscriber: object | None = None  # Discord delivery (set_client ở bot)

    def require(self, name: str) -> object:
        """Trả về singleton đã wire; raise RuntimeError nếu bootstrap() chưa chạy."""
        value = getattr(self, name)
        if value is None:
            raise RuntimeError("bootstrap() has not been called")
        return value

    def reset(self) -> None:
        """Đưa toàn bộ singleton về None (tests only)."""
        for f in fields(self):
            setattr(self, f.name, None)

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in fields(self))
