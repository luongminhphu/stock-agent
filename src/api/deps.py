"""FastAPI dependency injection.

Owner: api segment.
Provides reusable Depends() callables for all routes.

    get_db()              — yields AsyncSession (commit/rollback)
    get_current_user_id() — Wave 1: X-User-Id header | Wave 2: JWT
    get_quote_service()   — returns singleton QuoteService
"""
from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.db import AsyncSessionLocal
from src.platform.bootstrap import get_quote_service as _get_qs
from src.market.quote_service import QuoteService


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Provide a DB session. Commits on success, rolls back on error."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_current_user_id(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> str:
    """Extract user ID from request.

    Wave 1: reads X-User-Id header (no auth, dev/internal only).
    Wave 2: replace with JWT Bearer token verification.
    """
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header is required (Wave 1 auth).",
        )
    return x_user_id


def get_quote_service() -> QuoteService:
    """Return the singleton QuoteService (wired at bootstrap)."""
    return _get_qs()
