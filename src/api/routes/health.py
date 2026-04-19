"""Health routes — liveness + readiness probes.

Owner: api segment (thin adapter over platform.health).

GET /health      — liveness probe (always 200 if process is alive)
GET /ready       — readiness probe (200 only when bootstrap + DB are healthy)
GET /api/v1/me   — returns owner_user_id from settings (used by dashboard JS)
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.platform.config import settings
from src.platform.health import HealthStatus, check_liveness, check_readiness

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
async def liveness() -> JSONResponse:
    """Returns 200 as long as the process is running."""
    report = await check_liveness()
    return JSONResponse(
        status_code=200,
        content={"status": report.status, "checks": report.checks},
    )


@router.get("/ready", summary="Readiness probe")
async def readiness() -> JSONResponse:
    """Returns 200 only when DB is reachable and all singletons are initialised."""
    report = await check_readiness()
    status_code = 200 if report.status == HealthStatus.OK else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": report.status, "checks": report.checks},
    )


@router.get("/api/v1/me", summary="Current owner identity", tags=["auth"])
async def get_me() -> JSONResponse:
    """Returns the owner_user_id configured via OWNER_USER_ID env var.

    Used by the dashboard frontend to resolve the correct user_id
    without hardcoding it in JS.
    """
    return JSONResponse(
        status_code=200,
        content={"user_id": settings.owner_user_id},
    )
