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

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from src.api.routes.briefing import router as briefing_router
from src.api.routes.health import router as health_router
from src.api.routes.market import router as market_router
from src.api.routes.readmodel import router as readmodel_router
from src.api.routes.thesis import router as thesis_router
from src.api.routes.watchlist import router as watchlist_router
from src.platform.bootstrap import bootstrap
from src.platform.config import settings
from src.platform.logging import get_logger

logger = get_logger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_DASHBOARD_DIR = _STATIC_DIR / "dashboard"
_DASHBOARD_HTML = _DASHBOARD_DIR / "index.html"

# Inline SVG favicon — no file needed, no 404.
_FAVICON_SVG = (
    b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
    b"<text y='.9em' font-size='90'>\xf0\x9f\x93\x88</text></svg>"
)

# Cache-Control values
# - HTML entry point: no-store so Cloudflare/browser always fetches fresh.
#   This is the anchor of the cache busting strategy: when index.html is fresh,
#   the JS/CSS ?v= query strings change per deploy, forcing sub-resource refetch.
# - JS / CSS: no-cache + must-revalidate — browser revalidates every request
#   via ETag/Last-Modified. 304 if unchanged (no body sent).
# - Everything else (images, fonts): 10-minute cache — safe default.
_CACHE_NO_STORE       = "no-store, no-cache, must-revalidate"
_CACHE_MUST_REVALIDATE = "no-cache, must-revalidate"
_CACHE_SHORT           = "public, max-age=600"


class _CacheBustedStaticFiles(StaticFiles):
    """StaticFiles with explicit Cache-Control headers.

    - JS / CSS / HTML: ``no-cache, must-revalidate`` — browser revalidates
      every request via ETag/Last-Modified. If unchanged, server returns 304.
      This ensures stale JS/CSS/HTML is never served after a deploy.
    - Everything else: 10-minute cache — safe default for images/fonts.
    """

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:  # type: ignore[override]
        async def _send_with_cache_header(message: Any) -> None:
            if message["type"] == "http.response.start":
                path: str = scope.get("path", "")
                if path.endswith((".js", ".css", ".html")):
                    cache_value = _CACHE_MUST_REVALIDATE.encode()
                else:
                    cache_value = _CACHE_SHORT.encode()
                headers = list(message.get("headers", []))
                headers = [
                    (k, v) for k, v in headers if k.lower() != b"cache-control"
                ]
                headers.append((b"cache-control", cache_value))
                message = {**message, "headers": headers}
            await send(message)

        await super().__call__(scope, receive, _send_with_cache_header)


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

    # Favicon — inline SVG, never 404.
    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(
            content=_FAVICON_SVG,
            media_type="image/svg+xml",
            headers={"cache-control": "public, max-age=86400"},
        )

    # Mount static assets with proper Cache-Control headers.
    # JS/CSS/HTML use no-cache+must-revalidate so browsers always revalidate after deploy.
    if _STATIC_DIR.exists():
        app.mount("/static", _CacheBustedStaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/dashboard", include_in_schema=False)
    async def serve_dashboard() -> FileResponse:
        """Serve static dashboard shell with no-store cache header.

        no-store prevents Cloudflare and the browser from caching index.html.
        This is the anchor: fresh HTML means the browser sees updated JS ?v= URLs
        on every deploy, so stale sub-resources are never silently reused.

        HTML is at src/api/static/dashboard/index.html.
        API data is fetched client-side from /api/v1/readmodel/dashboard/{user_id}/...
        """
        return FileResponse(
            _DASHBOARD_HTML,
            headers={"cache-control": _CACHE_NO_STORE},
        )

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
