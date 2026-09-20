"""Application bootstrap — run once at startup.

Owner: platform segment.
Called by both API lifespan and bot on_ready.

Guarantees:
    - Idempotent: safe to call multiple times (singletons are initialised only once).
    - Fast in test environment: mock adapter selected, no real HTTP clients.
    - All get_*() raise RuntimeError if called before bootstrap().

Lifecycle:
    await bootstrap()   — call on startup (API lifespan / bot on_ready)
    await shutdown()    — call on teardown (API lifespan / bot on_close)
"""

from __future__ import annotations

from typing import Any

from src.platform.container import AppContainer
from src.platform.logging import configure_logging, get_logger

logger = get_logger(__name__)

container = AppContainer()


async def bootstrap() -> None:
    """Initialise all application singletons. Idempotent."""
    configure_logging()

    if container.quote_service is None:
        from src.market.adapters.factory import build_adapter
        from src.market.quote_service import QuoteService, TradingHoursGuard
        from src.platform.config import get_settings
        from src.platform.db import AsyncSessionLocal as _QS_SessionLocal

        _settings = get_settings()
        _guard = TradingHoursGuard.from_settings(_settings)
        container.quote_service = QuoteService(
            build_adapter(),
            guard=_guard,
            session_factory=_QS_SessionLocal,
        )
        logger.info("platform.bootstrap.quote_service_ready")

    if container.market_regime_service is None:
        from src.market.market_regime import MarketRegimeService

        # Singleton so the 3-minute TTL cache in MarketRegimeService is
        # actually shared across /pretrade calls instead of being rebuilt
        # (and re-fetched) on every command invocation.
        container.market_regime_service = MarketRegimeService(container.quote_service)
        logger.info("platform.bootstrap.market_regime_service_ready")

    # ── SymbolRegistry: dynamic engine init (HTTP + DB, async) ────────────────
    # Always runs on first bootstrap — idempotent within TTL window.
    # Runs after quote_service so session_factory is safely importable.
    from src.market.registry import registry as _symbol_registry
    from src.platform.config import get_settings as _get_settings
    from src.platform.db import AsyncSessionLocal as _AsyncSessionLocal

    _reg_settings = _get_settings()
    if _reg_settings.is_test or _reg_settings.mock_market:
        # Test / mock mode: khong goi vnstock (HTTP ~30s khi khong co mang);
        # registry chay tren _STATIC_SEED — du cho HPG/VCB/VNM/... trong tests.
        logger.info(
            "platform.bootstrap.symbol_registry_static_only",
            size=_symbol_registry.size(),
            environment=_reg_settings.environment,
            mock_market=_reg_settings.mock_market,
        )
    else:
        from src.readmodel.universe_query import make_known_tickers_provider

        await _symbol_registry.initialize(
            known_tickers_provider=make_known_tickers_provider(_AsyncSessionLocal)
        )
        logger.info(
            "platform.bootstrap.symbol_registry_ready",
            size=_symbol_registry.size(),
        )

    if container.ohlcv_service is None:
        from src.market.adapters.vci_ohlcv import VCIOHLCVAdapter
        from src.market.ohlcv_service import OHLCVService

        container.ohlcv_service = OHLCVService(adapter=VCIOHLCVAdapter())
        logger.info("platform.bootstrap.ohlcv_service_ready")

    if container.ticker_context_service is None:
        from src.market.ticker_context import TickerContextService

        container.ticker_context_service = TickerContextService(
            quote_service=container.quote_service,  # type: ignore[arg-type]
            ohlcv_service=container.ohlcv_service,  # type: ignore[arg-type]
        )
        logger.info("platform.bootstrap.ticker_context_service_ready")

    if container.ai_client is None:
        from src.ai.client import AIClient
        from src.platform.config import settings

        container.ai_client = AIClient(api_key=settings.perplexity_api_key)
        logger.info("platform.bootstrap.ai_client_ready")

    if container.thesis_review_agent is None:
        from src.ai.agents.thesis_review import ThesisReviewAgent

        container.thesis_review_agent = ThesisReviewAgent(client=container.ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.thesis_review_agent_ready")

    if container.thesis_debate_agent is None:
        from src.ai.agents.thesis_debate import ThesisDebateAgent

        container.thesis_debate_agent = ThesisDebateAgent(ai_client=container.ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.thesis_debate_agent_ready")

    if container.thesis_suggest_agent is None:
        from src.ai.agents.suggest_agent import ThesisSuggestAgent

        container.thesis_suggest_agent = ThesisSuggestAgent(client=container.ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.thesis_suggest_agent_ready")

    if container.briefing_agent is None:
        from src.ai.agents.briefing import BriefingAgent

        container.briefing_agent = BriefingAgent(client=container.ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.briefing_agent_ready")

    if container.why_agent is None:
        from src.ai.agents.why import WhyAgent

        container.why_agent = WhyAgent(client=container.ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.why_agent_ready")

    if container.pretrade_agent is None:
        from src.ai.agents.pretrade import PreTradeAgent

        container.pretrade_agent = PreTradeAgent(client=container.ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.pretrade_agent_ready")

    if container.stress_test_agent is None:
        from src.ai.agents.stress_test import StressTestAgent

        container.stress_test_agent = StressTestAgent(client=container.ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.stress_test_agent_ready")

    if container.replay_agent is None:
        from src.ai.agents.replay import ReplayAgent

        container.replay_agent = ReplayAgent(ai_client=container.ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.replay_agent_ready")

    if container.sector_rotation_agent is None:
        from src.ai.agents.sector_rotation import SectorRotationAgent

        container.sector_rotation_agent = SectorRotationAgent(ai_client=container.ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.sector_rotation_agent_ready")

    if container.snapshot_scheduler is None:
        from src.market.snapshot_scheduler import SnapshotScheduler
        from src.platform.db import AsyncSessionLocal

        if container.session_factory is None:
            container.session_factory = AsyncSessionLocal
            logger.info("platform.bootstrap.session_factory_cached")

        from src.thesis.snapshot_job import run_snapshot_job

        _qs = container.quote_service
        container.snapshot_scheduler = SnapshotScheduler(
            job=lambda: run_snapshot_job(_qs, AsyncSessionLocal),
        )
        logger.info("platform.bootstrap.snapshot_scheduler_ready")

    if container.session_factory is None:
        from src.platform.db import AsyncSessionLocal

        container.session_factory = AsyncSessionLocal
        logger.info("platform.bootstrap.session_factory_cached")

    if container.pnl_service_class is None:
        from src.portfolio.pnl_service import PnlService

        container.pnl_service_class = PnlService
        logger.info("platform.bootstrap.pnl_service_ready")

    if container.investor_profile_service is None:
        from src.ai.memory.investor_profile import InvestorProfileService
        from src.platform.config import settings

        user_id = getattr(settings, "scheduler_user_id", None)
        if user_id:
            container.investor_profile_service = (InvestorProfileService, str(user_id))
            logger.info(
                "platform.bootstrap.investor_profile_service_ready",
                user_id=str(user_id),
            )
        else:
            logger.warning(
                "platform.bootstrap.investor_profile_service_skipped",
                reason="scheduler_user_id not configured",
            )

    if container.memory_consolidator is None:
        from src.ai.memory.consolidator import MemoryConsolidator
        from src.platform.config import settings

        user_id = getattr(settings, "scheduler_user_id", None)
        if user_id:
            container.memory_consolidator = MemoryConsolidator(
                client=container.ai_client,  # type: ignore[arg-type]
                user_id=str(user_id),
            )
            logger.info(
                "platform.bootstrap.memory_consolidator_ready",
                user_id=str(user_id),
            )
        else:
            logger.warning(
                "platform.bootstrap.memory_consolidator_skipped",
                reason="scheduler_user_id not configured",
            )

    if container.agenda_builder_agent is None:
        from src.ai.agents.agenda_builder import AgendaBuilderAgent

        container.agenda_builder_agent = AgendaBuilderAgent(ai_client=container.ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.agenda_builder_agent_ready")

    if container.agenda_service_factory is None:
        from src.platform.config import settings

        user_id = getattr(settings, "scheduler_user_id", None)
        if user_id:
            from src.ai.memory.memory_service import MemoryService
            from src.briefing.agenda_service import AgendaService

            _agent_ref = container.agenda_builder_agent
            container.agenda_service_factory = lambda session: AgendaService(  # noqa: E731
                session=session,
                agenda_agent=_agent_ref,
                memory_service=MemoryService,
            )
            logger.info(
                "platform.bootstrap.agenda_service_factory_ready",
                user_id=str(user_id),
            )
        else:
            logger.warning(
                "platform.bootstrap.agenda_service_factory_skipped",
                reason="scheduler_user_id not configured",
            )

    # ── Wave 2 (market): TrendReasoningAgent ─────────────────────────────────
    if container.trend_reasoning_agent is None:
        from src.ai.agents.trend_reasoning import TrendReasoningAgent

        container.trend_reasoning_agent = TrendReasoningAgent(client=container.ai_client)
        logger.info("platform.bootstrap.trend_reasoning_agent_ready")

    # ── Wave 2b: SignalEngineAgent ───────────────────────────────────────────
    if container.signal_engine_agent is None:
        from src.ai.agents.signal_engine import SignalEngineAgent

        container.signal_engine_agent = SignalEngineAgent(ai_client=container.ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.signal_engine_agent_ready")

    # ── Wave D.1: TrendSnapshotStore (readmodel) — persisted baseline ──────────
    if container.trend_snapshot_store is None:
        from src.platform.db import AsyncSessionLocal
        from src.readmodel.trend_snapshot_store import TrendSnapshotStore

        container.trend_snapshot_store = TrendSnapshotStore(session_factory=AsyncSessionLocal)
        logger.info("platform.bootstrap.trend_snapshot_store_ready")

    # ── Trend Prediction: TrendPredictionStore (readmodel) ──────────────────
    if container.trend_prediction_store is None:
        from src.platform.db import AsyncSessionLocal
        from src.readmodel.trend_prediction_store import TrendPredictionStore

        container.trend_prediction_store = TrendPredictionStore(
            session_factory=AsyncSessionLocal,
        )
        logger.info("platform.bootstrap.trend_prediction_store_ready")

    # ── W1: RecentReviewsStore (readmodel) ──────────────────────────────────
    if container.recent_reviews_store is None:
        from src.platform.db import AsyncSessionLocal
        from src.readmodel.recent_reviews_store import RecentReviewsStore

        container.recent_reviews_store = RecentReviewsStore(
            session_factory=AsyncSessionLocal,
        )
        logger.info("platform.bootstrap.recent_reviews_store_ready")

    # ── W3: PortfolioQueryAdapter (readmodel) ──────────────────────────────
    if container.portfolio_query_adapter is None:
        from src.platform.db import AsyncSessionLocal
        from src.readmodel.portfolio_query_service import PortfolioQueryAdapter

        container.portfolio_query_adapter = PortfolioQueryAdapter(
            session_factory=AsyncSessionLocal,
        )
        logger.info("platform.bootstrap.portfolio_query_adapter_ready")

    # ── Event Bus + subscribers (start bus FIRST) ───────────────────────────
    from src.platform.event_bus import get_event_bus

    bus = get_event_bus()
    await bus.start()
    logger.info("platform.bootstrap.event_bus_ready")

    # ── Wave 3 (readmodel): cache invalidation hooks ─────────────────────────
    from src.readmodel import CacheSubscriber

    CacheSubscriber.register()
    logger.info("platform.bootstrap.cache_subscriber_ready")

    # ── Gap 2 (readmodel): IntelligenceSnapshotSubscriber ───────────────────
    if container.intelligence_snapshot_subscriber is None:
        from src.readmodel import IntelligenceSnapshotSubscriber

        IntelligenceSnapshotSubscriber.register()
        container.intelligence_snapshot_subscriber = IntelligenceSnapshotSubscriber
        logger.info("platform.bootstrap.intelligence_snapshot_subscriber_ready")

    # ── readmodel: GlobalRiskSubscriber — project IE verdict into memory store
    if container.global_risk_subscriber is None:
        from src.readmodel.global_risk_subscriber import GlobalRiskSubscriber

        GlobalRiskSubscriber.register()
        container.global_risk_subscriber = GlobalRiskSubscriber
        logger.info("platform.bootstrap.global_risk_subscriber_ready")

    # ── Wave D.1: Warm-up all persisted in-memory stores from DB ─────────────
    # Run after all stores are initialised and before any scheduler fires.
    # Gives agents & briefing context from the last cycle without waiting for
    # the first scheduler run post-restart.
    await _warm_up_persisted_stores(
        trend_snapshot_store=container.trend_snapshot_store,
        trend_prediction_store=container.trend_prediction_store,
        session_factory=container.session_factory,
    )

    if container.proactive_alert_agent is None:
        from src.ai.agents.proactive_alert_agent import get_proactive_alert_agent
        from src.platform.db import AsyncSessionLocal

        container.proactive_alert_agent = get_proactive_alert_agent(
            ai_client=container.ai_client,  # type: ignore[arg-type]
            session_factory=AsyncSessionLocal,
        )
        container.proactive_alert_agent.register()
        logger.info("platform.bootstrap.proactive_alert_agent_ready")

    if container.thesis_review_listener is None:
        from src.platform.db import AsyncSessionLocal
        from src.thesis.thesis_review_listener import ThesisReviewListener

        container.thesis_review_listener = ThesisReviewListener(
            session_factory=AsyncSessionLocal,
            review_agent=container.thesis_review_agent,
            quote_service=container.quote_service,
            ticker_context_service=container.ticker_context_service,
        )
        container.thesis_review_listener.register()
        logger.info("platform.bootstrap.thesis_review_listener_ready")

    # ── Wave C: SignalEngine → ThesisReview bridge ──────────────────────────
    if container.signal_review_trigger_listener is None:
        from src.platform.db import AsyncSessionLocal
        from src.thesis.signal_review_trigger_listener import SignalReviewTriggerListener

        container.signal_review_trigger_listener = SignalReviewTriggerListener(
            session_factory=AsyncSessionLocal,
            review_agent=container.thesis_review_agent,
            quote_service=container.quote_service,
            ticker_context_service=container.ticker_context_service,
        )
        container.signal_review_trigger_listener.register()
        logger.info("platform.bootstrap.signal_review_trigger_listener_ready")

    if container.briefing_listener is None:
        from src.bot.brief_delivery import DiscordBriefDelivery
        from src.briefing.briefing_listener import BriefingListener
        from src.platform.config import settings

        user_id = getattr(settings, "scheduler_user_id", None)
        if user_id:
            morning_id = getattr(settings, "morning_channel_id", None)
            eod_id = getattr(settings, "eod_channel_id", None)
            container.briefing_listener = BriefingListener(
                user_id=str(user_id),
                delivery=DiscordBriefDelivery(
                    morning_channel_id=int(morning_id) if morning_id else None,
                    eod_channel_id=int(eod_id) if eod_id else None,
                ),
                agenda_service_factory=container.agenda_service_factory,
            )
            container.briefing_listener.register()
            logger.info(
                "platform.bootstrap.briefing_listener_ready",
                user_id=str(user_id),
                agenda_service_wired=container.agenda_service_factory is not None,
            )
        else:
            logger.warning(
                "platform.bootstrap.briefing_listener_skipped",
                reason="scheduler_user_id not configured",
            )

    # ── G4: StressTest → Watchlist trigger bridge ───────────────────────────
    if container.stress_test_subscriber is None:
        from src.platform.db import AsyncSessionLocal
        from src.watchlist.stress_test_subscriber import StressTestSubscriber

        container.stress_test_subscriber = StressTestSubscriber(session_factory=AsyncSessionLocal)
        container.stress_test_subscriber.register()
        logger.info("platform.bootstrap.stress_test_subscriber_ready")

    # ── Wave 3: OpportunityScreenScheduler + subscriber ─────────────────────
    if container.opportunity_screen_scheduler is None:
        from src.market.opportunity_screen_scheduler import OpportunityScreenScheduler

        container.opportunity_screen_scheduler = OpportunityScreenScheduler(
            quote_service=container.quote_service,
        )
        logger.info("platform.bootstrap.opportunity_screen_scheduler_ready")

    if container.opportunity_screen_subscriber is None:
        from src.market.opportunity_screen_subscriber import OpportunityScreenSubscriber

        container.opportunity_screen_subscriber = OpportunityScreenSubscriber()
        container.opportunity_screen_subscriber.register()
        logger.info("platform.bootstrap.opportunity_screen_subscriber_ready")

    # ── Wave 3: OpportunityAnalysisHandler (ai segment) ───────────────────────────
    if container.opportunity_analysis_handler is None:
        from src.ai.opportunity_analysis_handler import get_opportunity_analysis_handler
        from src.platform.db import AsyncSessionLocal

        container.opportunity_analysis_handler = get_opportunity_analysis_handler(
            ai_client=container.ai_client,  # type: ignore[arg-type]
            session_factory=AsyncSessionLocal,
        )
        container.opportunity_analysis_handler.register()
        logger.info("platform.bootstrap.opportunity_analysis_handler_ready")

    # ── Proactive Discovery: portfolio-aware market scan + AI synthesis ────────
    if container.proactive_discovery_service is None:
        from src.ai.agents.proactive_discovery import ProactiveDiscoveryAgent
        from src.market.proactive_discovery_service import ProactiveDiscoveryService
        from src.market.registry import registry as _reg
        from src.platform.db import AsyncSessionLocal
        from src.readmodel.portfolio_query_service import PortfolioQueryService

        async def _portfolio_provider(user_id: str) -> dict[str, Any]:
            async with AsyncSessionLocal() as session:
                return await PortfolioQueryService(session).get_portfolio(user_id=user_id)

        _discovery_agent = ProactiveDiscoveryAgent(ai_client=container.ai_client)
        container.proactive_discovery_service = ProactiveDiscoveryService(
            ai_agent=_discovery_agent,
            quote_service=container.quote_service,
            registry=_reg,  # dynamic singleton
            portfolio_provider=_portfolio_provider,
        )
        logger.info("platform.bootstrap.proactive_discovery_service_ready")

    # ── Wave B2: SignalEngineListener — fully wired with portfolio context ────
    if container.signal_engine_listener is None:
        from src.ai.signal_engine_listener import SignalEngineListener
        from src.platform.db import AsyncSessionLocal
        from src.thesis.stress_test_query_service import ThesisRiskSignalQuery
        from src.thesis.thesis_query_service import ThesisActiveContextQuery
        from src.watchlist.watchlist_query_service import WatchlistQueryService

        container.signal_engine_listener = SignalEngineListener(
            ai_client=container.ai_client,  # type: ignore[arg-type]
            watchdog_service=WatchlistQueryService(session_factory=AsyncSessionLocal),
            stress_test_service=ThesisRiskSignalQuery(session_factory=AsyncSessionLocal),
            thesis_query=ThesisActiveContextQuery(session_factory=AsyncSessionLocal),
            portfolio_query=container.portfolio_query_adapter,
            feedback_service=None,
        )
        container.signal_engine_listener.register()
        logger.info("platform.bootstrap.signal_engine_listener_ready")

    # ── Trend Prediction: TrendEngineListener ───────────────────────────────
    if container.trend_engine_listener is None:
        from src.ai.trend_engine_listener import TrendEngineListener
        from src.market.trend_engine import TrendEngine
        from src.platform.db import AsyncSessionLocal
        from src.thesis.thesis_query_service import ThesisActiveContextQuery
        from src.watchlist.watchlist_query_service import WatchlistQueryService

        _trend_engine = TrendEngine(
            ohlcv_service=container.ohlcv_service,
        )
        container.trend_engine_listener = TrendEngineListener(
            trend_reasoning_agent=container.trend_reasoning_agent,  # type: ignore[arg-type]
            trend_engine=_trend_engine,
            prediction_store=container.trend_prediction_store,  # type: ignore[arg-type]
            watchlist_query=WatchlistQueryService(session_factory=AsyncSessionLocal),
            thesis_query=ThesisActiveContextQuery(session_factory=AsyncSessionLocal),
        )
        container.trend_engine_listener.register()
        logger.info("platform.bootstrap.trend_engine_listener_ready")

    # ── Wave E: PostMortemService + MemoryInjectionListener ─────────────────
    if container.post_mortem_service is None:
        from src.platform.db import AsyncSessionLocal
        from src.thesis.post_mortem_service import PostMortemService

        container.post_mortem_service = PostMortemService(
            ai_client=container.ai_client,  # type: ignore[arg-type]
            session_factory=AsyncSessionLocal,
        )
        container.post_mortem_service.register()
        logger.info("platform.bootstrap.post_mortem_service_ready")

    if container.memory_injection_listener is None:
        from src.ai.memory_injection_listener import MemoryInjectionListener
        from src.platform.db import AsyncSessionLocal

        container.memory_injection_listener = MemoryInjectionListener(
            session_factory=AsyncSessionLocal,
        )
        container.memory_injection_listener.register()
        logger.info("platform.bootstrap.memory_injection_listener_ready")

    # ── core: IntelligenceEngineListener (Wave 2 AI active) ─────────────────
    if container.intelligence_engine_listener is None:
        from src.ai.agents.intelligence_verdict import IntelligenceVerdictAgent
        from src.core.intelligence_listener import IntelligenceEngineListener

        _intelligence_verdict_agent = IntelligenceVerdictAgent(
            ai_client=container.ai_client  # type: ignore[arg-type]
        )
        container.intelligence_engine_listener = IntelligenceEngineListener(
            verdict_agent=_intelligence_verdict_agent,
        )
        container.intelligence_engine_listener.register()
        logger.info("platform.bootstrap.intelligence_engine_listener_ready")

    # ── bot: IntelligenceEngineSubscriber (Discord delivery) ────────────────
    if container.intelligence_engine_subscriber is None:
        from src.bot.intelligence_engine_subscriber import IntelligenceEngineSubscriber
        from src.platform.config import settings

        raw_channel = settings.alert_channel_id
        channel_id = int(raw_channel) if raw_channel else None
        container.intelligence_engine_subscriber = IntelligenceEngineSubscriber(
            channel_id=channel_id
        )
        container.intelligence_engine_subscriber.register()
        logger.info(
            "platform.bootstrap.intelligence_engine_subscriber_ready",
            discord_channel_id=channel_id,
        )

    # ── core: EngineFeedbackListener ────────────────────────────────────────
    if container.engine_feedback_listener is None:
        from src.core.feedback_listener import EngineFeedbackListener

        container.engine_feedback_listener = EngineFeedbackListener()
        container.engine_feedback_listener.register()
        logger.info("platform.bootstrap.engine_feedback_listener_ready")

    # ── core: UserActionFeedbackListener — closes the feedback loop ──────────
    if container.user_action_listener is None:
        from src.core.user_action_listener import UserActionFeedbackListener

        container.user_action_listener = UserActionFeedbackListener()
        container.user_action_listener.register()
        logger.info("platform.bootstrap.user_action_listener_ready")

    # ── ai.memory: FeedbackLedgerSubscriber — S6 ledger hợp nhất (Wave E3a) ──
    if container.feedback_ledger_subscriber is None:
        from src.ai.memory.feedback_ledger import FeedbackLedgerSubscriber

        container.feedback_ledger_subscriber = FeedbackLedgerSubscriber()
        container.feedback_ledger_subscriber.register()
        logger.info("platform.bootstrap.feedback_ledger_subscriber_ready")

    # ── watchlist: ProactiveWatchListener — closes the proactive watch chain ─
    # ProactiveWatchScheduler (bot) fires ProactiveWatchRequestedEvent 3x/day
    # (09:15 / 11:15 / 14:15 ICT). Without this registration the event bus
    # silently drops every request and no intraday proactive alert is ever sent.
    if container.proactive_watch_listener is None:
        from src.platform.db import AsyncSessionLocal
        from src.watchlist.proactive_watch_listener import ProactiveWatchListener

        container.proactive_watch_listener = ProactiveWatchListener(
            quote_service=container.quote_service,
            session_factory=AsyncSessionLocal,
            ticker_context_service=container.ticker_context_service,
        )
        container.proactive_watch_listener.register()
        logger.info("platform.bootstrap.proactive_watch_listener_ready")

    # ── portfolio: PortfolioSnapshotListener — aggregates P&L on request ────
    if container.portfolio_snapshot_listener is None:
        from src.portfolio.snapshot_listener import PortfolioSnapshotListener

        container.portfolio_snapshot_listener = PortfolioSnapshotListener()
        container.portfolio_snapshot_listener.register()
        logger.info("platform.bootstrap.portfolio_snapshot_listener_ready")

    logger.info("platform.bootstrap.complete")


async def _warm_up_persisted_stores(
    trend_snapshot_store: Any,
    trend_prediction_store: Any,
    session_factory: Any,
) -> None:
    """Load all persisted in-memory stores from DB on startup.

    Called once after all singletons are ready and before any scheduler fires.
    Failures are logged and swallowed — warm-up is best-effort; stores fall
    back gracefully to cold-start behaviour if DB is unavailable at boot.
    """
    from src.platform.db import AsyncSessionLocal

    sf = session_factory or AsyncSessionLocal

    # QuoteService — warm _last_known from DB (chống N/A sau restart)
    try:
        qs = get_quote_service()
        if hasattr(qs, "warm_load"):
            n = await qs.warm_load()
            logger.info("bootstrap.warm_up.quote_cache", loaded=n)
    except Exception as exc:
        logger.warning("bootstrap.warm_up.quote_cache_failed", error=str(exc))

    # TrendSnapshotStore
    try:
        if hasattr(trend_snapshot_store, "warm_load"):
            n = await trend_snapshot_store.warm_load()
            logger.info("bootstrap.warm_up.trend_snapshots", loaded=n)
    except Exception as exc:
        logger.warning("bootstrap.warm_up.trend_snapshots_failed", error=str(exc))

    # TrendPredictionStore (readmodel)
    try:
        if hasattr(trend_prediction_store, "warm_load"):
            n = await trend_prediction_store.warm_load()
            logger.info("bootstrap.warm_up.trend_predictions", loaded=n)
    except Exception as exc:
        logger.warning("bootstrap.warm_up.trend_predictions_failed", error=str(exc))

    # IntelligenceSnapshotStore
    try:
        from src.readmodel.intelligence_snapshot import get_intelligence_snapshot

        snap_store = get_intelligence_snapshot()
        if hasattr(snap_store, "container.session_factory") and snap_store._session_factory is None:
            snap_store._session_factory = sf
        if hasattr(snap_store, "warm_load"):
            n = await snap_store.warm_load()
            logger.info("bootstrap.warm_up.intelligence_snapshots", loaded=n)
    except Exception as exc:
        logger.warning("bootstrap.warm_up.intelligence_snapshots_failed", error=str(exc))

    # GlobalRiskStore
    try:
        from src.readmodel.global_risk_store import get_global_risk_store

        risk_store = get_global_risk_store()
        if hasattr(risk_store, "container.session_factory") and risk_store._session_factory is None:
            risk_store._session_factory = sf
        if hasattr(risk_store, "warm_load"):
            n = await risk_store.warm_load()
            logger.info("bootstrap.warm_up.global_risk_snapshots", loaded=n)
    except Exception as exc:
        logger.warning("bootstrap.warm_up.global_risk_snapshots_failed", error=str(exc))

    # AgendaCache (today only)
    try:
        from src.briefing.agenda_cache import warm_load_agendas

        n = await warm_load_agendas(sf)
        logger.info("bootstrap.warm_up.daily_agendas", loaded=n)
    except Exception as exc:
        logger.warning("bootstrap.warm_up.daily_agendas_failed", error=str(exc))

    logger.info("bootstrap.warm_up.complete")


async def shutdown() -> None:
    """Gracefully release resources held by singletons."""

    if container.snapshot_scheduler is not None:
        try:
            await container.snapshot_scheduler.stop()  # type: ignore[attr-defined]
            logger.info("platform.shutdown.snapshot_scheduler_stopped")
        except Exception as exc:
            logger.warning("platform.shutdown.snapshot_scheduler_failed", error=str(exc))

    if container.opportunity_screen_scheduler is not None:
        try:
            await container.opportunity_screen_scheduler.stop()  # type: ignore[attr-defined]
            logger.info("platform.shutdown.opportunity_screen_scheduler_stopped")
        except Exception as exc:
            logger.warning("platform.shutdown.opportunity_screen_scheduler_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Getters — raise RuntimeError if called before bootstrap()
# ---------------------------------------------------------------------------


def get_quote_service() -> Any:
    return container.require("quote_service")


def get_ohlcv_service() -> Any:
    return container.require("ohlcv_service")


def get_ticker_context_service() -> Any:
    return container.require("ticker_context_service")


def get_market_regime_service() -> Any:
    return container.require("market_regime_service")


def get_ai_client() -> Any:
    return container.require("ai_client")


def get_thesis_review_agent() -> Any:
    return container.require("thesis_review_agent")


def get_thesis_debate_agent() -> Any:
    return container.require("thesis_debate_agent")


def get_thesis_suggest_agent() -> Any:
    return container.require("thesis_suggest_agent")


def get_briefing_agent() -> Any:
    return container.require("briefing_agent")


def get_why_agent() -> Any:
    return container.require("why_agent")


def get_pretrade_agent() -> Any:
    return container.require("pretrade_agent")


def get_stress_test_agent() -> Any:
    return container.require("stress_test_agent")


def get_replay_agent() -> Any:
    return container.require("replay_agent")


def get_sector_rotation_agent() -> Any:
    return container.require("sector_rotation_agent")


def get_snapshot_scheduler() -> Any:
    return container.require("snapshot_scheduler")


def get_pnl_service_class() -> Any:
    return container.require("pnl_service_class")


def get_pnl_service() -> Any:
    """Alias for get_pnl_service_class() — returns the PnlService class."""
    return get_pnl_service_class()


def get_session_factory() -> Any:
    if container.session_factory is None:
        raise RuntimeError("bootstrap() has not been called")
    return container.session_factory


def get_trend_prediction_store() -> Any:
    if container.trend_prediction_store is None:
        raise RuntimeError("bootstrap() has not been called")
    return container.trend_prediction_store


def get_trend_reasoning_agent() -> Any:
    if container.trend_reasoning_agent is None:
        raise RuntimeError("bootstrap() has not been called")
    return container.trend_reasoning_agent


def get_opportunity_screen_scheduler() -> Any:
    if container.opportunity_screen_scheduler is None:
        raise RuntimeError("bootstrap() has not been called")
    return container.opportunity_screen_scheduler


def get_opportunity_screen_subscriber() -> Any:
    if container.opportunity_screen_subscriber is None:
        raise RuntimeError("bootstrap() has not been called")
    return container.opportunity_screen_subscriber


def get_recent_reviews_store() -> Any:
    if container.recent_reviews_store is None:
        raise RuntimeError("bootstrap() has not been called")
    return container.recent_reviews_store


def get_portfolio_query_adapter() -> Any:
    if container.portfolio_query_adapter is None:
        raise RuntimeError("bootstrap() has not been called")
    return container.portfolio_query_adapter


def get_agenda_service_factory() -> Any:
    if container.agenda_service_factory is None:
        raise RuntimeError("bootstrap() has not been called")
    return container.agenda_service_factory


def get_investor_profile_service() -> tuple[Any, ...] | None:
    """Return (InvestorProfileService class, user_id str) or None if not initialised.

    Returns None (not raises) when scheduler_user_id is not configured —
    callers must handle the None case gracefully.
    """
    return container.investor_profile_service


def get_memory_consolidator() -> Any:
    """Return the MemoryConsolidator singleton or None if not initialised.

    Returns None (not raises) when scheduler_user_id is not configured —
    callers must handle the None case gracefully.
    """
    return container.memory_consolidator


def get_briefing_listener() -> Any:
    """Return the BriefingListener singleton or None if not initialised.

    Returns None (not raises) when scheduler_user_id is not configured —
    callers must handle the None case gracefully.
    """
    return container.briefing_listener


def get_intelligence_engine_subscriber() -> Any:
    """Return the IntelligenceEngineSubscriber singleton or None if not initialised.

    Returns None (not raises) — bot caller checks for None before calling set_client().
    """
    return container.intelligence_engine_subscriber


def get_proactive_discovery_service() -> Any:
    """Return ProactiveDiscoveryService singleton or None if not initialised."""
    return container.proactive_discovery_service


def get_trend_snapshot_store() -> Any:
    """Return TrendSnapshotStore singleton (Wave D.1 — DB-backed)."""
    return container.require("trend_snapshot_store")


# ---------------------------------------------------------------------------
# Test hook — reset toan bo singleton ve None (chi dung trong tests)
# ---------------------------------------------------------------------------


def reset_singletons() -> None:
    """Reset all bootstrap singletons to ``None`` so ``bootstrap()`` re-wires from scratch.

    Tests only. Also resets the global EventBus so listeners registered by a
    previous ``bootstrap()`` do not leak across tests.
    """
    container.reset()
    from src.platform.event_bus import reset_event_bus

    reset_event_bus()


def __getattr__(name: str) -> object:
    """Compat 1 wave: ``bootstrap._quote_service`` → ``container.quote_service`` (chỉ đọc)."""
    if name.startswith("_") and name[1:] in container.field_names:
        return getattr(container, name[1:])
    raise AttributeError(name)
