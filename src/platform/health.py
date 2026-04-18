"""Health check — liveness + readiness probes.

Owner: platform segment.

Liveness  : always returns quickly (no I/O).
Readiness : checks DB connectivity AND whether bootstrap singletons are up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    checks: dict[str, str] = field(default_factory=dict)


async def check_liveness() -> HealthReport:
    """Fast liveness probe — no I/O, just confirms process is alive."""
    return HealthReport(status=HealthStatus.OK, checks={"process": HealthStatus.OK})


async def check_readiness() -> HealthReport:
    """Readiness probe — DB ping + bootstrap singleton check."""
    checks: dict[str, str] = {}

    # 1. DB connectivity
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = HealthStatus.OK
    except Exception as exc:
        logger.error("health.db_check_failed", error=str(exc))
        checks["database"] = HealthStatus.DOWN

    # 2. Bootstrap singletons initialised?
    try:
        from src.platform import bootstrap as _bs

        checks["quote_service"] = (
            HealthStatus.OK if _bs._quote_service is not None else HealthStatus.DOWN
        )
        checks["perplexity_client"] = (
            HealthStatus.OK if _bs._perplexity_client is not None else HealthStatus.DOWN
        )
        checks["thesis_review_agent"] = (
            HealthStatus.OK if _bs._thesis_review_agent is not None else HealthStatus.DOWN
        )
        checks["briefing_agent"] = (
            HealthStatus.OK if _bs._briefing_agent is not None else HealthStatus.DOWN
        )
    except Exception as exc:
        logger.error("health.bootstrap_check_failed", error=str(exc))
        checks["bootstrap"] = HealthStatus.DOWN

    overall = (
        HealthStatus.OK
        if all(v == HealthStatus.OK for v in checks.values())
        else HealthStatus.DEGRADED
    )
    return HealthReport(status=overall, checks=checks)


# Backward-compat alias used by existing /health route
async def check_health() -> HealthReport:
    return await check_readiness()
