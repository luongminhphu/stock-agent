"""Application bootstrap — run once at startup.

Owner: platform segment.
Called by both API lifespan and bot on_ready.

Guarantees:
    - Idempotent: safe to call multiple times (singletons are initialised only once).
    - Fast in test environment: mock adapter selected, no real HTTP clients.
    - All get_*() raise RuntimeError if called before bootstrap().
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
_investor_profile_service: object | None = None  # Wave 1 — Blueprint V2

# PnlService is session-scoped (stateless), so bootstrap stores the class
# rather than an instance. get_pnl_service() returns the class; callers
# instantiate with their own session: PnlService(session).
_pnl_service_class: type | None = None


async def bootstrap() -> None:
    """Initialise all application singletons. Idempotent."""
    configure_logging()

    global _quote_service, _ohlcv_service, _ai_client, _thesis_review_agent
    global _thesis_suggest_agent, _briefing_agent, _why_agent, _pretrade_agent
    global _stress_test_agent, _replay_agent, _snapshot_scheduler
    global _sector_rotation_agent, _investor_profile_service, _pnl_service_class

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

        # SnapshotScheduler.__init__() takes no args — it lazily calls
        # get_quote_service() inside _run_snapshot at task execution time.
        _snapshot_scheduler = SnapshotScheduler()
        logger.info("platform.bootstrap.snapshot_scheduler_ready")

    if _pnl_service_class is None:
        from src.portfolio.pnl_service import PnlService

        _pnl_service_class = PnlService
        logger.info("platform.bootstrap.pnl_service_ready")


# ---------------------------------------------------------------------------
# Getters — raise RuntimeError if called before bootstrap()
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


# Backward-compat alias — tests and legacy callers still use get_perplexity_client()
get_perplexity_client = get_ai_client


def get_thesis_review_agent():
    if _thesis_review_agent is None:
        raise RuntimeError("ThesisReviewAgent not initialised — call bootstrap() first.")
    return _thesis_review_agent


def get_thesis_suggest_agent():
    if _thesis_suggest_agent is None:
        raise RuntimeError("ThesisSuggestAgent not initialised — call bootstrap() first.")
    return _thesis_suggest_agent


# Alias — scheduler_trigger and other callers import get_suggest_agent directly
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
    """Return the PnlService class (not an instance).

    PnlService is session-scoped. Callers must instantiate with their session:
        pnl_svc = get_pnl_service()(session)
    """
    if _pnl_service_class is None:
        raise RuntimeError("PnlService not initialised — call bootstrap() first.")
    return _pnl_service_class
