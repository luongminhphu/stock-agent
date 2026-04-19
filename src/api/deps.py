"""FastAPI dependency injection.

Owner: api segment.
Provides reusable Depends() callables for all routes.
"""

from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.db import AsyncSessionLocal
from src.platform.bootstrap import (
    get_briefing_agent as _get_briefing_agent,
    get_quote_service as _get_qs,
    get_thesis_review_agent as _get_agent,
    get_thesis_suggest_agent as _get_suggest_agent,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_current_user_id() -> str:
    """Return the single owner's user_id from settings.

    Single-user app: no session/auth needed.
    OWNER_USER_ID in .env must match the Discord user ID of the owner.
    """
    from src.platform.config import settings

    if not settings.owner_user_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OWNER_USER_ID not configured. Set it in .env.",
        )
    return settings.owner_user_id


def get_quote_service() -> object:
    return _get_qs()


def get_thesis_review_agent() -> object:
    return _get_agent()


def get_thesis_suggest_agent() -> object:
    """Return the ThesisSuggestAgent singleton (initialised at bootstrap)."""
    return _get_suggest_agent()


def get_briefing_agent() -> object:
    return _get_briefing_agent()


async def get_thesis_service(
    session: AsyncSession = Depends(get_db),
) -> "ThesisService":  # type: ignore[name-defined]  # noqa: F821
    from src.thesis.service import ThesisService

    return ThesisService(session=session)


async def get_review_service(
    session: AsyncSession = Depends(get_db),
    agent: object = Depends(get_thesis_review_agent),
    quote_svc: object = Depends(get_quote_service),
) -> "ReviewService":  # type: ignore[name-defined]  # noqa: F821
    from src.thesis.review_service import ReviewService

    return ReviewService(session=session, agent=agent, quote_service=quote_svc)  # type: ignore[arg-type]


async def get_briefing_service(
    session: AsyncSession = Depends(get_db),
    quote_svc: object = Depends(get_quote_service),
    briefing_agent: object = Depends(get_briefing_agent),
) -> "BriefingService":  # type: ignore[name-defined]  # noqa: F821
    from src.briefing.service import BriefingService
    from src.watchlist.service import WatchlistService

    watchlist_service = WatchlistService(session=session)
    return BriefingService(
        watchlist_service=watchlist_service,
        quote_service=quote_svc,
        briefing_agent=briefing_agent,
    )
