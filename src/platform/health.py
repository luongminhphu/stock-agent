from dataclasses import dataclass
from enum import Enum

from sqlalchemy import text

from src.platform.db import AsyncSessionLocal
from src.platform.logging import get_logger

logger = get_logger(__name__)


class HealthStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass
class HealthReport:
    status: HealthStatus
    checks: dict[str, str]


async def check_health() -> HealthReport:
    """Run all health checks and return a consolidated report."""
    checks: dict[str, str] = {}

    # DB check
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = HealthStatus.OK
    except Exception as exc:
        logger.error("health.db_check_failed", error=str(exc))
        checks["database"] = HealthStatus.DOWN

    overall = (
        HealthStatus.OK
        if all(v == HealthStatus.OK for v in checks.values())
        else HealthStatus.DEGRADED
    )

    return HealthReport(status=overall, checks=checks)
