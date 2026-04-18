"""Health check routes.

GET /health        — liveness probe (always 200 if process is alive)
GET /health/ready  — readiness probe (checks DB connectivity)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db
from src.api.dto.health import HealthResponse, ReadinessResponse
from src.platform.config import settings

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    """Liveness probe — returns 200 as long as the process is running."""
    return HealthResponse(status="ok", env=settings.environment, version="0.1.0")


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness(db: AsyncSession = Depends(get_db)) -> ReadinessResponse:
    """Readiness probe — checks DB connectivity before accepting traffic."""
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    status = "ready" if db_ok else "not_ready"
    return ReadinessResponse(status=status, db=db_ok)
