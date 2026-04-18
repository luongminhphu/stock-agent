from src.platform.logging import configure_logging, get_logger


async def bootstrap() -> None:
    """Run once at application startup. Call this from bot/app.py and api/app.py."""
    configure_logging()
    logger = get_logger(__name__)
    logger.info("bootstrap.complete")
