"""FastAPI application factory.

Owner: api segment.
This module wires the HTTP layer only.
No business logic — all domain work is delegated to segment services.

Route groups:
    /health                — liveness + readiness probes
    /dashboard             — static dashboard shell (wave 3 UI)
    /static/dashboard/     — CSS + JS assets for the shell
    /api/v1/market         — quote, OHLCV
    /api/v1/thesis         — thesis CRUD + review
    /api/v1/watchlist      — watchlist management
    /api/v1/briefing       — on-demand brief generation
    /api/v1/readmodel      — dashboard, leaderboard, thesis timeline (wave 3)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.platform.bootstrap import bootstrap
from src.platform.config import settings
from src.platform.logging import get_logger
from src.api.routes.briefing import router as briefing_router
from src.api.routes.health import router as health_router
from src.api.routes.market import router as market_router
from src.api.routes.readmodel import router as readmodel_router
from src.api.routes.thesis import router as thesis_router
from src.api.routes.watchlist import router as watchlist_router

logger = get_logger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_DASHBOARD_DIR = _STATIC_DIR / "dashboard"
_DASHBOARD_HTML = _DASHBOARD_DIR / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Run startup/shutdown logic around the app lifetime."""
    await bootstrap()
    logger.info("api.startup", env=settings.environment)
    yield
    logger.info("api.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="stock-agent",
        version="0.1.0",
        description="AI-native stock analysis platform for HOSE, HNX, UPCoM.",
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
        # Disable automatic trailing-slash redirect so POST/PUT/PATCH requests
        # with a body are never silently redirected (307 drops the body in most clients).
        redirect_slashes=False,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount static assets (CSS, JS, fonts, …)
    # /static/dashboard/dashboard.css
    # /static/dashboard/dashboard.js
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/dashboard", include_in_schema=False)
    async def serve_dashboard() -> FileResponse:
        """Serve static dashboard shell.

        HTML is at src/api/static/dashboard/index.html.
        API data is fetched client-side from /api/v1/readmodel/dashboard/{user_id}/...
        """
        return FileResponse(_DASHBOARD_HTML)

    # Register routers — order matters for OpenAPI grouping
    app.include_router(health_router)
    app.include_router(market_router, prefix="/api/v1")
    app.include_router(thesis_router, prefix="/api/v1")
    app.include_router(watchlist_router, prefix="/api/v1")
    app.include_router(briefing_router, prefix="/api/v1")
    app.include_router(readmodel_router, prefix="/api/v1")

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request, exc: Exception) -> JSONResponse:
        logger.error("api.unhandled_exception", path=str(request.url), error=str(exc))
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    return app


# ASGI entry point: uvicorn src.api.app:app
app = create_app()
