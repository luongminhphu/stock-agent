"""Application bootstrap — run once at startup.

Owner: platform segment.
Called by both API lifespan and bot on_ready.
"""
from __future__ import annotations

from src.platform.logging import configure_logging, get_logger

logger = get_logger(__name__)

_quote_service: object | None = None
_perplexity_client: object | None = None
_thesis_review_agent: object | None = None
_briefing_agent: object | None = None


async def bootstrap() -> None:
    configure_logging()

    global _quote_service, _perplexity_client, _thesis_review_agent, _briefing_agent

    if _quote_service is None:
        from src.market.adapters.factory import build_adapter
        from src.market.quote_service import QuoteService

        _quote_service = QuoteService(build_adapter())
        logger.info("platform.bootstrap.quote_service_ready")

    if _perplexity_client is None:
        from src.ai.client import PerplexityClient
        from src.platform.config import settings

        _perplexity_client = PerplexityClient(api_key=settings.perplexity_api_key)
        logger.info("platform.bootstrap.perplexity_client_ready")

    if _thesis_review_agent is None:
        from src.ai.agents.thesis_review import ThesisReviewAgent

        _thesis_review_agent = ThesisReviewAgent(client=_perplexity_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.thesis_review_agent_ready")

    if _briefing_agent is None:
        from src.ai.agents.briefing import BriefingAgent

        _briefing_agent = BriefingAgent(client=_perplexity_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.briefing_agent_ready")

    logger.info("platform.bootstrap.ok")


def get_quote_service() -> object:
    if _quote_service is None:
        raise RuntimeError("QuoteService not initialised — call bootstrap() first.")
    return _quote_service


def get_perplexity_client() -> object:
    if _perplexity_client is None:
        raise RuntimeError("PerplexityClient not initialised — call bootstrap() first.")
    return _perplexity_client


def get_thesis_review_agent() -> object:
    if _thesis_review_agent is None:
        raise RuntimeError("ThesisReviewAgent not initialised — call bootstrap() first.")
    return _thesis_review_agent


def get_briefing_agent() -> object:
    if _briefing_agent is None:
        raise RuntimeError("BriefingAgent not initialised — call bootstrap() first.")
    return _briefing_agent
