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

from src.platform.logging import configure_logging, get_logger

logger = get_logger(__name__)

_quote_service: object | None = None
_ohlcv_service: object | None = None
_ai_client: object | None = None
_thesis_review_agent: object | None = None
_thesis_suggest_agent: object | None = None
_briefing_agent: object | None = None
_why_agent: object | None = None
_pretrade_agent: object | None = None
_stress_test_agent: object | None = None
_replay_agent: object | None = None
_snapshot_scheduler: object | None = None
_sector_rotation_agent: object | None = None
_investor_profile_service: tuple | None = None
_memory_consolidator: object | None = None
_proactive_alert_agent: object | None = None
_thesis_review_listener: object | None = None
_briefing_listener: object | None = None
_stress_test_subscriber: object | None = None  # G4: StressTest → Watchlist bridge
_opportunity_screen_scheduler: object | None = None  # Wave 3
_opportunity_screen_subscriber: object | None = None  # Wave 3
_signal_engine_agent: object | None = None   # Wave 2b: cross-check engine
_signal_engine_listener: object | None = None  # Wave B2: fully wired
_agenda_builder_agent: object | None = None   # AgendaBuilderAgent singleton
_agenda_service_factory: object | None = None  # callable(session) -> AgendaService | None

_pnl_service_class: type | None = None


async def bootstrap() -> None:
    """Initialise all application singletons. Idempotent."""
    configure_logging()

    global _quote_service, _ohlcv_service, _ai_client, _thesis_review_agent
    global _thesis_suggest_agent, _briefing_agent, _why_agent, _pretrade_agent
    global _stress_test_agent, _replay_agent, _snapshot_scheduler
    global _sector_rotation_agent, _investor_profile_service, _pnl_service_class
    global _memory_consolidator, _proactive_alert_agent, _thesis_review_listener
    global _briefing_listener, _stress_test_subscriber
    global _opportunity_screen_scheduler, _opportunity_screen_subscriber
    global _signal_engine_agent, _signal_engine_listener
    global _agenda_builder_agent, _agenda_service_factory

    if _quote_service is None:
        from src.market.adapters.factory import build_adapter
        from src.market.quote_service import QuoteService

        _quote_service = QuoteService(build_adapter())
        logger.info("platform.bootstrap.quote_service_ready")

    if _ohlcv_service is None:
        from src.market.adapters.vci_ohlcv import VCIOHLCVAdapter
        from src.market.ohlcv_service import OHLCVService

        _ohlcv_service = OHLCVService(adapter=VCIOHLCVAdapter())
        logger.info("platform.bootstrap.ohlcv_service_ready")

    if _ai_client is None:
        from src.ai.client import AIClient
        from src.platform.config import settings

        _ai_client = AIClient(api_key=settings.perplexity_api_key)
        logger.info("platform.bootstrap.ai_client_ready")

    if _thesis_review_agent is None:
        from src.ai.agents.thesis_review import ThesisReviewAgent

        _thesis_review_agent = ThesisReviewAgent(client=_ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.thesis_review_agent_ready")

    if _thesis_suggest_agent is None:
        from src.ai.agents.suggest_agent import ThesisSuggestAgent

        _thesis_suggest_agent = ThesisSuggestAgent(client=_ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.thesis_suggest_agent_ready")

    if _briefing_agent is None:
        from src.ai.agents.briefing import BriefingAgent

        _briefing_agent = BriefingAgent(client=_ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.briefing_agent_ready")

    if _why_agent is None:
        from src.ai.agents.why import WhyAgent

        _why_agent = WhyAgent(client=_ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.why_agent_ready")

    if _pretrade_agent is None:
        from src.ai.agents.pretrade import PreTradeAgent

        _pretrade_agent = PreTradeAgent(client=_ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.pretrade_agent_ready")

    if _stress_test_agent is None:
        from src.ai.agents.stress_test import StressTestAgent

        _stress_test_agent = StressTestAgent(client=_ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.stress_test_agent_ready")

    if _replay_agent is None:
        from src.ai.agents.replay import ReplayAgent

        _replay_agent = ReplayAgent(ai_client=_ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.replay_agent_ready")

    if _sector_rotation_agent is None:
        from src.ai.agents.sector_rotation import SectorRotationAgent

        _sector_rotation_agent = SectorRotationAgent(ai_client=_ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.sector_rotation_agent_ready")

    if _snapshot_scheduler is None:
        from src.market.snapshot_scheduler import SnapshotScheduler
        from src.platform.db import AsyncSessionLocal

        _snapshot_scheduler = SnapshotScheduler(
            quote_service=_quote_service,
            session_factory=AsyncSessionLocal,
        )
        logger.info("platform.bootstrap.snapshot_scheduler_ready")

    if _pnl_service_class is None:
        from src.portfolio.pnl_service import PnlService

        _pnl_service_class = PnlService
        logger.info("platform.bootstrap.pnl_service_ready")

    if _investor_profile_service is None:
        from src.platform.config import settings
        from src.platform.investor_profile import InvestorProfileService

        user_id = getattr(settings, "scheduler_user_id", None)
        if user_id:
            _investor_profile_service = (InvestorProfileService, str(user_id))
            logger.info(
                "platform.bootstrap.investor_profile_service_ready",
                user_id=str(user_id),
            )
        else:
            logger.warning(
                "platform.bootstrap.investor_profile_service_skipped",
                reason="scheduler_user_id not configured",
            )

    if _memory_consolidator is None:
        from src.ai.memory.consolidator import MemoryConsolidator
        from src.platform.config import settings

        user_id = getattr(settings, "scheduler_user_id", None)
        if user_id:
            _memory_consolidator = MemoryConsolidator(
                client=_ai_client,  # type: ignore[arg-type]
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

    if _agenda_builder_agent is None:
        from src.ai.agents.agenda_builder import AgendaBuilderAgent

        _agenda_builder_agent = AgendaBuilderAgent(ai_client=_ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.agenda_builder_agent_ready")

    if _agenda_service_factory is None:
        from src.platform.config import settings

        user_id = getattr(settings, "scheduler_user_id", None)
        if user_id:
            from src.ai.memory.memory_service import MemoryService
            from src.briefing.agenda_service import AgendaService

            _agent_ref = _agenda_builder_agent
            _agenda_service_factory = lambda session: AgendaService(  # noqa: E731
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

    # ── Wave 2b: SignalEngineAgent ────────────────────────────────────────────
    if _signal_engine_agent is None:
        from src.ai.agents.signal_engine import SignalEngineAgent

        _signal_engine_agent = SignalEngineAgent(ai_client=_ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.signal_engine_agent_ready")

    # ── Event Bus + subscribers (start bus FIRST) ─────────────────────────────
    from src.platform.event_bus import get_event_bus
    bus = get_event_bus()
    await bus.start()
    logger.info("platform.bootstrap.event_bus_ready")

    # ── Wave 3 (readmodel): cache invalidation hooks ──────────────────────────
    # Register immediately after bus.start() so CacheSubscriber handlers
    # are wired before any other subscriber that might emit scan/briefing events.
    # Idempotent — safe to call from both API lifespan and bot on_ready.
    from src.readmodel import CacheSubscriber
    CacheSubscriber.register()
    logger.info("platform.bootstrap.cache_subscriber_ready")

    if _proactive_alert_agent is None:
        from src.ai.agents.proactive_alert_agent import get_proactive_alert_agent
        from src.platform.db import AsyncSessionLocal

        _proactive_alert_agent = get_proactive_alert_agent(
            ai_client=_ai_client,  # type: ignore[arg-type]
            session_factory=AsyncSessionLocal,
        )
        _proactive_alert_agent.register()
        logger.info("platform.bootstrap.proactive_alert_agent_ready")

    if _thesis_review_listener is None:
        from src.thesis.thesis_review_listener import ThesisReviewListener
        from src.platform.db import AsyncSessionLocal

        _thesis_review_listener = ThesisReviewListener(
            session_factory=AsyncSessionLocal,
            review_agent=_thesis_review_agent,
            quote_service=_quote_service,
        )
        _thesis_review_listener.register()
        logger.info("platform.bootstrap.thesis_review_listener_ready")

    if _briefing_listener is None:
        from src.briefing.briefing_listener import BriefingListener
        from src.platform.config import settings

        user_id = getattr(settings, "scheduler_user_id", None)
        if user_id:
            morning_id = getattr(settings, "morning_channel_id", None)
            eod_id = getattr(settings, "eod_channel_id", None)
            _briefing_listener = BriefingListener(
                morning_channel_id=int(morning_id) if morning_id else None,
                eod_channel_id=int(eod_id) if eod_id else None,
                user_id=str(user_id),
            )
            _briefing_listener.register()
            logger.info(
                "platform.bootstrap.briefing_listener_ready",
                user_id=str(user_id),
            )
        else:
            logger.warning(
                "platform.bootstrap.briefing_listener_skipped",
                reason="scheduler_user_id not configured",
            )

    # ── G4: StressTest → Watchlist trigger bridge ───────────────────────────
    if _stress_test_subscriber is None:
        from src.watchlist.stress_test_subscriber import StressTestSubscriber
        from src.platform.db import AsyncSessionLocal

        _stress_test_subscriber = StressTestSubscriber(session_factory=AsyncSessionLocal)
        _stress_test_subscriber.register()
        logger.info("platform.bootstrap.stress_test_subscriber_ready")

    # ── Wave 3: OpportunityScreenScheduler + subscriber ───────────────────────
    # Subscriber registered here (bus already started).
    # Scheduler is initialised here but start() is called by bot on_ready —
    # discord.ext.tasks requires the bot event loop to be running.
    #
    # Chain:
    #   OpportunityScreenScheduler._run()  [09:10 ICT daily]
    #     → run_opportunity_screen_job()   [market segment]
    #       → OpportunityScreenCompletedEvent → EventBus
    #         → OpportunityScreenSubscriber._handle()  [market segment stub]
    #           → (Wave 3 TODO) AI cross-check → Discord morning channel
    if _opportunity_screen_scheduler is None:
        from src.market.opportunity_screen_scheduler import OpportunityScreenScheduler

        _opportunity_screen_scheduler = OpportunityScreenScheduler(
            quote_service=_quote_service,
        )
        logger.info("platform.bootstrap.opportunity_screen_scheduler_ready")

    if _opportunity_screen_subscriber is None:
        from src.market.opportunity_screen_subscriber import OpportunityScreenSubscriber

        _opportunity_screen_subscriber = OpportunityScreenSubscriber()
        _opportunity_screen_subscriber.register()
        logger.info("platform.bootstrap.opportunity_screen_subscriber_ready")

    # ── Wave B2: SignalEngineListener ───────────────────────────────────────────
    # All 3 required deps are now available as session_factory-backed singletons.
    # portfolio_query and feedback_service remain optional (None = degraded gracefully).
    if _signal_engine_listener is None:
        from src.ai.signal_engine_listener import SignalEngineListener
        from src.thesis.watchlist_query_service import WatchlistQueryService
        from src.thesis.stress_test_query_service import StressTestQueryService
        from src.thesis.thesis_query_service import ThesisQueryService
        from src.platform.db import AsyncSessionLocal

        _signal_engine_listener = SignalEngineListener(
            ai_client=_ai_client,  # type: ignore[arg-type]
            watchdog_service=WatchlistQueryService(session_factory=AsyncSessionLocal),
            stress_test_service=StressTestQueryService(session_factory=AsyncSessionLocal),
            thesis_query=ThesisQueryService(session_factory=AsyncSessionLocal),
            portfolio_query=None,   # Wave B3: wire when PortfolioQueryService ready
            feedback_service=None,  # Wave B3: wire when FeedbackService ready
        )
        _signal_engine_listener.register()
        logger.info("platform.bootstrap.signal_engine_listener_ready")

    logger.info("platform.bootstrap.complete")


async def shutdown() -> None:
    """Gracefully release resources held by singletons.

    Call this in the API lifespan teardown and in the bot on_close handler.
    Safe to call even if bootstrap() was never called (all singletons are None).
    """
    global _quote_service, _ohlcv_service, _ai_client
    global _snapshot_scheduler, _opportunity_screen_scheduler

    if _snapshot_scheduler is not None:
        try:
            await _snapshot_scheduler.stop()  # type: ignore[attr-defined]
            logger.info("platform.shutdown.snapshot_scheduler_stopped")
        except Exception as exc:
            logger.warning("platform.shutdown.snapshot_scheduler_failed", error=str(exc))

    if _opportunity_screen_scheduler is not None:
        try:
            await _opportunity_screen_scheduler.stop()  # type: ignore[attr-defined]
            logger.info("platform.shutdown.opportunity_screen_scheduler_stopped")
        except Exception as exc:
            logger.warning("platform.shutdown.opportunity_screen_scheduler_failed", error=str(exc))

    try:
        from src.platform.event_bus import get_event_bus
        bus = get_event_bus()
        await bus.stop()
        dead = getattr(bus, "dead_letters", [])
        if dead:
            logger.warning(
                "platform.shutdown.event_bus_dead_letters",
                count=len(dead),
                entries=[str(d) for d in dead],
            )
        logger.info("platform.shutdown.event_bus_stopped")
    except Exception as exc:
        logger.warning("platform.shutdown.event_bus_stop_failed", error=str(exc))

    if _quote_service is not None:
        try:
            await _quote_service.close()  # type: ignore[union-attr]
            logger.info("platform.shutdown.quote_service_closed")
        except Exception as exc:
            logger.warning("platform.shutdown.quote_service_close_failed", error=str(exc))

    if _ohlcv_service is not None:
        try:
            await _ohlcv_service.close()  # type: ignore[union-attr]
            logger.info("platform.shutdown.ohlcv_service_closed")
        except Exception as exc:
            logger.warning("platform.shutdown.ohlcv_service_close_failed", error=str(exc))

    if _ai_client is not None:
        try:
            await _ai_client.aclose()  # type: ignore[union-attr]
            logger.info("platform.shutdown.ai_client_closed")
        except Exception as exc:
            logger.warning("platform.shutdown.ai_client_close_failed", error=str(exc))

    logger.info("platform.shutdown.complete")


# ---------------------------------------------------------------------------
# Getters
# ---------------------------------------------------------------------------

def get_quote_service():
    if _quote_service is None:
        raise RuntimeError("QuoteService not initialised — call bootstrap() first.")
    return _quote_service


def get_ohlcv_service():
    if _ohlcv_service is None:
        raise RuntimeError("OHLCVService not initialised — call bootstrap() first.")
    return _ohlcv_service


def get_ai_client():
    if _ai_client is None:
        raise RuntimeError("AIClient not initialised — call bootstrap() first.")
    return _ai_client


get_perplexity_client = get_ai_client


def get_thesis_review_agent():
    if _thesis_review_agent is None:
        raise RuntimeError("ThesisReviewAgent not initialised — call bootstrap() first.")
    return _thesis_review_agent


def get_thesis_suggest_agent():
    if _thesis_suggest_agent is None:
        raise RuntimeError("ThesisSuggestAgent not initialised — call bootstrap() first.")
    return _thesis_suggest_agent


get_suggest_agent = get_thesis_suggest_agent


def get_briefing_agent():
    if _briefing_agent is None:
        raise RuntimeError("BriefingAgent not initialised — call bootstrap() first.")
    return _briefing_agent


def get_why_agent():
    if _why_agent is None:
        raise RuntimeError("WhyAgent not initialised — call bootstrap() first.")
    return _why_agent


def get_pretrade_agent():
    if _pretrade_agent is None:
        raise RuntimeError("PreTradeAgent not initialised — call bootstrap() first.")
    return _pretrade_agent


def get_stress_test_agent():
    if _stress_test_agent is None:
        raise RuntimeError("StressTestAgent not initialised — call bootstrap() first.")
    return _stress_test_agent


def get_replay_agent():
    if _replay_agent is None:
        raise RuntimeError("ReplayAgent not initialised — call bootstrap() first.")
    return _replay_agent


def get_sector_rotation_agent():
    if _sector_rotation_agent is None:
        raise RuntimeError("SectorRotationAgent not initialised — call bootstrap() first.")
    return _sector_rotation_agent


def get_snapshot_scheduler():
    if _snapshot_scheduler is None:
        raise RuntimeError("SnapshotScheduler not initialised — call bootstrap() first.")
    return _snapshot_scheduler


def get_pnl_service():
    """Return a factory: factory(session) -> PnlService."""
    if _pnl_service_class is None:
        raise RuntimeError("PnlService not initialised — call bootstrap() first.")
    quote_svc = get_quote_service()
    return lambda session: _pnl_service_class(session, quote_svc)  # type: ignore[misc]


def get_pnl_service_class():
    if _pnl_service_class is None:
        raise RuntimeError("PnlService not initialised — call bootstrap() first.")
    return _pnl_service_class


def get_investor_profile_service() -> tuple | None:
    return _investor_profile_service


def get_memory_consolidator():
    return _memory_consolidator


def get_agenda_service_factory():
    """Return factory callable(session) -> AgendaService, or None if not configured."""
    return _agenda_service_factory


def get_proactive_alert_agent():
    if _proactive_alert_agent is None:
        raise RuntimeError("ProactiveAlertAgent not initialised — call bootstrap() first.")
    return _proactive_alert_agent


def get_thesis_review_listener():
    if _thesis_review_listener is None:
        raise RuntimeError("ThesisReviewListener not initialised — call bootstrap() first.")
    return _thesis_review_listener


def get_briefing_listener():
    """Return BriefingListener singleton, or None if scheduler_user_id is not set."""
    return _briefing_listener


def get_stress_test_subscriber():
    """Return StressTestSubscriber singleton, or None if not initialised."""
    return _stress_test_subscriber


def get_signal_engine_agent():
    if _signal_engine_agent is None:
        raise RuntimeError("SignalEngineAgent not initialised — call bootstrap() first.")
    return _signal_engine_agent


def get_signal_engine_listener():
    """Return SignalEngineListener singleton, or None if not initialised."""
    return _signal_engine_listener


def get_opportunity_screen_scheduler():
    """Return the OpportunityScreenScheduler singleton.

    Call scheduler.start() in bot on_ready AFTER bootstrap() returns.
    Returns None if bootstrap() has not been called yet.
    """
    return _opportunity_screen_scheduler


def get_opportunity_screen_subscriber():
    """Return the OpportunityScreenSubscriber singleton.

    Call subscriber.set_client(bot) in bot on_ready to inject discord.Client.
    Returns None if not initialised.
    """
    return _opportunity_screen_subscriber
