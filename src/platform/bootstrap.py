"""Application bootstrap — run once at startup.

Owner: platform segment.
Called by both API lifespan and bot on_ready.

Provides:
    bootstrap()         — initialise all platform services
    get_quote_service() — return singleton QuoteService
"""
from __future__ import annotations

from src.platform.logging import configure_logging, get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Singletons — populated by bootstrap(), consumed via getters
# ---------------------------------------------------------------------------

_quote_service: "QuoteService | None" = None  # type: ignore[name-defined]  # noqa: F821


async def bootstrap() -> None:
    """Initialise logging and platform-level singletons.

    Safe to call multiple times — re-entrant via early return if already done.
    """
    configure_logging()

    global _quote_service
    if _quote_service is None:
        from src.market.adapters.factory import build_adapter
        from src.market.quote_service import QuoteService

        adapter = build_adapter()
        _quote_service = QuoteService(adapter)
        logger.info("platform.bootstrap.quote_service_ready")

    logger.info("platform.bootstrap.ok")


def get_quote_service() -> "QuoteService":  # noqa: F821
    """Return the singleton QuoteService.

    Raises RuntimeError if bootstrap() has not been called yet.
    """
    if _quote_service is None:
        raise RuntimeError(
            "QuoteService is not initialised. "
            "Ensure bootstrap() is awaited at application startup."
        )
    return _quote_service
