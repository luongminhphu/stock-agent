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
_trend_reasoning_agent: object | None = None  # TrendReasoningAgent singleton

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
    global _trend_reasoning_agent

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

    if _trend_reasoning_agent is None:
        from src.ai.agents.trend_reasoning import TrendReasoningAgent

        _trend_reasoning_agent = TrendReasoningAgent(client=_ai_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.trend_reasoning_agent_ready")

    # --- remaining singletons (agenda, opportunity screen, etc.) ---
    # These are wired lazily by their respective schedulers / listeners.
    # bootstrap() guarantees ai_client, ohlcv_service, and core agents are ready.


async def shutdown() -> None:
    """Graceful shutdown — close async clients."""
    global _ai_client
    if _ai_client is not None:
        from src.ai.client import AIClient
        if isinstance(_ai_client, AIClient):
            await _ai_client.aclose()
        logger.info("platform.bootstrap.ai_client_closed")


# ---------------------------------------------------------------------------
# Getters
# ---------------------------------------------------------------------------

def get_quote_service():
    if _quote_service is None:
        raise RuntimeError("bootstrap() has not been called")
    return _quote_service


def get_ohlcv_service():
    if _ohlcv_service is None:
        raise RuntimeError("bootstrap() has not been called")
    return _ohlcv_service


def get_ai_client():
    if _ai_client is None:
        raise RuntimeError("bootstrap() has not been called")
    return _ai_client


def get_thesis_review_agent():
    if _thesis_review_agent is None:
        raise RuntimeError("bootstrap() has not been called")
    return _thesis_review_agent


def get_thesis_suggest_agent():
    if _thesis_suggest_agent is None:
        raise RuntimeError("bootstrap() has not been called")
    return _thesis_suggest_agent


def get_briefing_agent():
    if _briefing_agent is None:
        raise RuntimeError("bootstrap() has not been called")
    return _briefing_agent


def get_why_agent():
    if _why_agent is None:
        raise RuntimeError("bootstrap() has not been called")
    return _why_agent


def get_pretrade_agent():
    if _pretrade_agent is None:
        raise RuntimeError("bootstrap() has not been called")
    return _pretrade_agent


def get_stress_test_agent():
    if _stress_test_agent is None:
        raise RuntimeError("bootstrap() has not been called")
    return _stress_test_agent


def get_replay_agent():
    if _replay_agent is None:
        raise RuntimeError("bootstrap() has not been called")
    return _replay_agent


def get_sector_rotation_agent():
    if _sector_rotation_agent is None:
        raise RuntimeError("bootstrap() has not been called")
    return _sector_rotation_agent


def get_snapshot_scheduler():
    return _snapshot_scheduler


def get_pnl_service_class():
    if _pnl_service_class is None:
        raise RuntimeError("bootstrap() has not been called")
    return _pnl_service_class


def get_investor_profile_service():
    return _investor_profile_service


def get_memory_consolidator():
    return _memory_consolidator


def get_proactive_alert_agent():
    return _proactive_alert_agent


def get_thesis_review_listener():
    return _thesis_review_listener


def get_briefing_listener():
    return _briefing_listener


def get_stress_test_subscriber():
    return _stress_test_subscriber


def get_opportunity_screen_scheduler():
    return _opportunity_screen_scheduler


def get_opportunity_screen_subscriber():
    return _opportunity_screen_subscriber


def get_signal_engine_agent():
    return _signal_engine_agent


def get_signal_engine_listener():
    return _signal_engine_listener


def get_agenda_builder_agent():
    return _agenda_builder_agent


def get_agenda_service_factory():
    return _agenda_service_factory


def get_trend_reasoning_agent():
    """Return TrendReasoningAgent singleton. None if bootstrap() not called yet."""
    return _trend_reasoning_agent
