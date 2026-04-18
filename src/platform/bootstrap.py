"""Application bootstrap — run once at startup.

Owner: platform segment.
Called by both API lifespan and bot on_ready.
"""
from __future__ import annotations

from src.platform.logging import configure_logging, get_logger

logger = get_logger(__name__)


async def bootstrap() -> None:
    """Initialise logging and any other platform-level setup."""
    configure_logging()
    logger.info("platform.bootstrap.ok")
