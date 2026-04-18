"""Platform segment — infra-level concerns only.

Public API:
    settings         — singleton Settings instance
    get_settings()   — factory (same singleton)
    Base             — SQLAlchemy DeclarativeBase for all models
    AsyncSessionLocal — session factory
    get_db_session() — async generator for FastAPI DI / context managers
    configure_logging() — call once at startup
    get_logger()     — get a named structlog logger
    bootstrap()      — async startup routine (logging + future: migrations)
    check_health()   — async health probe
"""

from src.platform.bootstrap import bootstrap
from src.platform.config import get_settings, settings
from src.platform.db import AsyncSessionLocal, Base, get_db_session
from src.platform.health import HealthReport, HealthStatus, check_health
from src.platform.logging import configure_logging, get_logger

__all__ = [
    "bootstrap",
    "get_settings",
    "settings",
    "AsyncSessionLocal",
    "Base",
    "get_db_session",
    "HealthReport",
    "HealthStatus",
    "check_health",
    "configure_logging",
    "get_logger",
]
