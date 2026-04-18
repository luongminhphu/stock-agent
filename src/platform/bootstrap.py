"""Application bootstrap — run once at startup.

Owner: platform segment.
Called by both API lifespan and bot on_ready.

Singletons provided:
    get_quote_service()        — QuoteService (market segment)
    get_perplexity_client()    — PerplexityClient (ai segment)
    get_thesis_review_agent()  — ThesisReviewAgent (ai segment)
"""
from __future__ import annotations

from src.platform.logging import configure_logging, get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

_quote_service: object | None = None
_perplexity_client: object | None = None
_thesis_review_agent: object | None = None


async def bootstrap() -> None:
    """Initialise all platform-level singletons.

    Re-entrant: safe to call multiple times.
    """
    configure_logging()

    global _quote_service, _perplexity_client, _thesis_review_agent

    # --- Market ---
    if _quote_service is None:
        from src.market.adapters.factory import build_adapter
        from src.market.quote_service import QuoteService

        _quote_service = QuoteService(build_adapter())
        logger.info("platform.bootstrap.quote_service_ready")

    # --- AI: Perplexity client ---
    if _perplexity_client is None:
        from src.ai.client import PerplexityClient
        from src.platform.config import settings

        _perplexity_client = PerplexityClient(api_key=settings.perplexity_api_key)
        logger.info("platform.bootstrap.perplexity_client_ready")

    # --- AI: ThesisReviewAgent ---
    if _thesis_review_agent is None:
        from src.ai.agents.thesis_review import ThesisReviewAgent

        _thesis_review_agent = ThesisReviewAgent(client=_perplexity_client)  # type: ignore[arg-type]
        logger.info("platform.bootstrap.thesis_review_agent_ready")

    logger.info("platform.bootstrap.ok")


# ---------------------------------------------------------------------------
# Getters — raise clearly if called before bootstrap()
# ---------------------------------------------------------------------------


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
