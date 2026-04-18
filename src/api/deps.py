"""FastAPI dependency injection.

Owner: api segment.
Provides reusable Depends() callables for all routes.

    get_db()                  — yields AsyncSession
    get_current_user_id()     — Wave 1: X-User-Id header | Wave 2: JWT
    get_quote_service()       — singleton QuoteService
    get_thesis_review_agent() — singleton ThesisReviewAgent
    get_review_service()      — per-request ReviewService (session + agent + quote)
"""
from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.db import AsyncSessionLocal
from src.platform.bootstrap import (
    get_quote_service as _get_qs,
    get_thesis_review_agent as _get_agent,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
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
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header is required (Wave 1 auth).",
        )
    return x_user_id


def get_quote_service() -> object:
    return _get_qs()


def get_thesis_review_agent() -> object:
    return _get_agent()


async def get_review_service(
    session: AsyncSession = Depends(get_db),
    agent: object = Depends(get_thesis_review_agent),
    quote_svc: object = Depends(get_quote_service),
) -> "ReviewService":  # type: ignore[name-defined]  # noqa: F821
    """Construct a per-request ReviewService with all dependencies injected."""
    from src.thesis.review_service import ReviewService

    return ReviewService(session=session, agent=agent, quote_service=quote_svc)  # type: ignore[arg-type]
