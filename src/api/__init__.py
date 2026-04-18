"""API segment — FastAPI HTTP adapter.

Public API:
    create_app()  — factory returning configured FastAPI instance
    app           — ASGI entry point (uvicorn src.api.app:app)

Routes:
    GET  /health             — liveness probe
    GET  /health/ready       — readiness probe (checks DB)
    GET  /api/v1/market/symbols/{ticker}
    GET  /api/v1/market/quote/{ticker}
    POST /api/v1/thesis
    GET  /api/v1/thesis
    GET  /api/v1/thesis/{id}
    DEL  /api/v1/thesis/{id}/close
    DEL  /api/v1/thesis/{id}/invalidate
    POST /api/v1/watchlist
    GET  /api/v1/watchlist
    DEL  /api/v1/watchlist/{ticker}

Rule: No domain logic in this segment.
      API = validate input → call service → serialize output.

Auth (Wave 1): X-User-Id header (dev only).
Auth (Wave 2): JWT Bearer token.
"""
from src.api.app import app, create_app

__all__ = ["app", "create_app"]
