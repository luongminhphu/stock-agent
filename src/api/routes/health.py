"""Health routes — liveness + readiness probes.

Owner: api segment (thin adapter over platform.health).

GET /health      — liveness probe (always 200 if process is alive)
GET /ready       — readiness probe (200 only when bootstrap + DB are healthy)
GET /api/v1/me   — current owner user context for thin clients
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from src.api.deps import get_current_user_id
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


@router.get("/api/v1/me", summary="Current API user context")
async def current_user_context(user_id: str = Depends(get_current_user_id)) -> dict[str, str]:
    """Expose resolved owner user id for thin clients like the dashboard shell."""
    return {"user_id": user_id}
