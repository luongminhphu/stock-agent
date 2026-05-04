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
_perplexity_client: object | None = None
_thesis_review_agent: object | None = None
_thesis_suggest_agent: object | None = None
_briefing_agent: object | None = None
_why_agent: object | None = None
_pretrade_agent: object | None = None
_stress_test_agent: object | None = None
_snapshot_scheduler: object | None = None
_pnl_service: object | None = None


async def bootstrap() -> None:
    """Initialise all application singletons. Idempotent."""
    configure_logging()

    global _quote_service, _ohlcv_service, _perplexity_client, _thesis_review_agent
    global _thesis_suggest_agent, _briefing_agent, _why_agent, _pretrade_agent
    global _stress_test_agent, _snapshot_scheduler, _pnl_service

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

    if _perplexity_client is None:
        from src.ai.client import PerplexityClient
        from src.platform.config import settings

        _perplexity_client = PerplexityClient(api_key=settings.perplexity_api_key)
        logger.info("platform.bootstrap.perplexity_client_ready")

    if _thesis_review_agent is None:
        from src.ai.agents.thesis_review import ThesisReviewAgent

        _thesis_review_agent = ThesisReviewAgent(client=_perplexity_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.thesis_review_agent_ready")

    if _thesis_suggest_agent is None:
        from src.ai.agents.suggest_agent import ThesisSuggestAgent

        _thesis_suggest_agent = ThesisSuggestAgent(client=_perplexity_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.thesis_suggest_agent_ready")

    if _briefing_agent is None:
        from src.ai.agents.briefing import BriefingAgent

        _briefing_agent = BriefingAgent(client=_perplexity_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.briefing_agent_ready")

    if _why_agent is None:
        from src.ai.agents.why import WhyAgent

        _why_agent = WhyAgent(client=_perplexity_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.why_agent_ready")

    if _pretrade_agent is None:
        from src.ai.agents.pretrade import PreTradeAgent

        _pretrade_agent = PreTradeAgent(client=_perplexity_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.pretrade_agent_ready")

    if _stress_test_agent is None:
        from src.ai.agents.stress_test import StressTestAgent

        _stress_test_agent = StressTestAgent(client=_perplexity_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.stress_test_agent_ready")

    # PnlService depends on quote_service — init last
    if _pnl_service is None:
        try:
            from src.portfolio.pnl_service import PnlService
            from src.platform.db import AsyncSessionLocal

            async with AsyncSessionLocal() as session:
                _pnl_service = PnlService(
                    session=session,
                    quote_service=_quote_service,  # type: ignore[arg-type]
                )
            logger.info("platform.bootstrap.pnl_service_ready")
        except Exception as exc:
            logger.warning("platform.bootstrap.pnl_service_skipped", error=str(exc))

    logger.info("platform.bootstrap.ok")


async def shutdown() -> None:
    """Graceful shutdown — close long-lived clients."""
    global _perplexity_client
    if _perplexity_client is not None:
        from src.ai.client import PerplexityClient

        if isinstance(_perplexity_client, PerplexityClient):
            await _perplexity_client.aclose()
            logger.info("platform.shutdown.perplexity_client_closed")


def reset_singletons() -> None:
    """Reset all singletons — for use in tests only."""
    global _quote_service, _ohlcv_service, _perplexity_client, _thesis_review_agent
    global _thesis_suggest_agent, _briefing_agent, _why_agent, _pretrade_agent
    global _stress_test_agent, _snapshot_scheduler, _pnl_service
    _quote_service = None
    _ohlcv_service = None
    _perplexity_client = None
    _thesis_review_agent = None
    _thesis_suggest_agent = None
    _briefing_agent = None
    _why_agent = None
    _pretrade_agent = None
    _stress_test_agent = None
    _snapshot_scheduler = None
    _pnl_service = None


def get_quote_service() -> object:
    if _quote_service is None:
        raise RuntimeError("QuoteService not initialised — call bootstrap() first.")
    return _quote_service


def get_ohlcv_service() -> object:
    if _ohlcv_service is None:
        raise RuntimeError("OHLCVService not initialised — call bootstrap() first.")
    return _ohlcv_service


def get_perplexity_client() -> object:
    if _perplexity_client is None:
        raise RuntimeError("PerplexityClient not initialised — call bootstrap() first.")
    return _perplexity_client


def get_thesis_review_agent() -> object:
    if _thesis_review_agent is None:
        raise RuntimeError("ThesisReviewAgent not initialised — call bootstrap() first.")
    return _thesis_review_agent


def get_thesis_suggest_agent() -> object:
    if _thesis_suggest_agent is None:
        raise RuntimeError("ThesisSuggestAgent not initialised — call bootstrap() first.")
    return _thesis_suggest_agent


def get_briefing_agent() -> object:
    if _briefing_agent is None:
        raise RuntimeError("BriefingAgent not initialised — call bootstrap() first.")
    return _briefing_agent


def get_why_agent() -> object:
    if _why_agent is None:
        raise RuntimeError("WhyAgent not initialised — call bootstrap() first.")
    return _why_agent


def get_pretrade_agent() -> object:
    if _pretrade_agent is None:
        raise RuntimeError("PreTradeAgent not initialised — call bootstrap() first.")
    return _pretrade_agent


def get_stress_test_agent() -> object:
    if _stress_test_agent is None:
        raise RuntimeError("StressTestAgent not initialised — call bootstrap() first.")
    return _stress_test_agent


def get_pnl_service() -> object | None:
    """Return PnlService singleton or None if not available."""
    return _pnl_service


def get_snapshot_scheduler() -> object:
    """Returns the SnapshotScheduler singleton (market segment)."""
    global _snapshot_scheduler
    if _snapshot_scheduler is None:
        from src.market.snapshot_scheduler import SnapshotScheduler

        _snapshot_scheduler = SnapshotScheduler()
    return _snapshot_scheduler
