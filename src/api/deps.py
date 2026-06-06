"""FastAPI dependency injection.

Owner: api segment.
Provides reusable Depends() callables for all routes.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.bootstrap import (
    get_briefing_agent as _get_briefing_agent,
)
from src.platform.bootstrap import (
    get_ohlcv_service as _get_ohlcv_svc,
)
from src.platform.bootstrap import (
    get_quote_service as _get_qs,
)
from src.platform.bootstrap import (
    get_replay_agent as _get_replay_agent,
)
from src.platform.bootstrap import (
    get_thesis_debate_agent as _get_debate_agent,
)
from src.platform.bootstrap import (
    get_thesis_review_agent as _get_agent,
)
from src.platform.bootstrap import (
    get_thesis_suggest_agent as _get_suggest_agent,
)
from src.platform.bootstrap import (
    get_ai_client as _get_ai_client,
)
from src.platform.db import AsyncSessionLocal


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


def get_ohlcv_service() -> object:
    """Return the OHLCVService singleton (initialised at bootstrap).

    Owner: market segment.
    Used by GET /market/ohlcv/{ticker}.
    """
    return _get_ohlcv_svc()


def get_thesis_review_agent() -> object:
    return _get_agent()


def get_thesis_suggest_agent() -> object:
    """Return the ThesisSuggestAgent singleton (initialised at bootstrap)."""
    return _get_suggest_agent()


def get_thesis_debate_agent() -> object:
    """Return the ThesisDebateAgent singleton (initialised at bootstrap)."""
    return _get_debate_agent()


def get_briefing_agent() -> object:
    return _get_briefing_agent()


def get_ai_client() -> object:
    """Return the AIClient singleton (initialised at bootstrap).

    Owner: ai segment.
    Routes that need to call AI directly (not via a dedicated agent) use this.
    """
    return _get_ai_client()


def get_symbol_registry() -> "SymbolRegistry":  # type: ignore[name-defined]  # noqa: F821
    """Return a SymbolRegistry instance for ticker → metadata resolution.

    Owner: market segment.
    Used by routes that need company_name / sector context before calling AI agents.
    SymbolRegistry is stateless — safe to instantiate per-request.
    """
    from src.market.registry import registry

    return registry


def get_breadth_service(
    quote_svc: object = Depends(get_quote_service),
) -> "BreadthService":  # type: ignore[name-defined]  # noqa: F821
    """DI factory for BreadthService.

    Owner: market segment.
    Used by GET /market/breadth.
    Stateless per-request — no session needed.
    """
    from src.market.breadth_service import BreadthService

    return BreadthService(quote_svc=quote_svc)  # type: ignore[arg-type]


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
        session=session,
    )


async def get_scan_service(
    session: AsyncSession = Depends(get_db),
    quote_svc: object = Depends(get_quote_service),
) -> "ScanService":  # type: ignore[name-defined]  # noqa: F821
    from src.watchlist.scan_service import ScanService

    return ScanService(session=session, quote_service=quote_svc)


async def get_timeline_service(
    session: AsyncSession = Depends(get_db),
) -> "ThesisTimelineService":  # type: ignore[name-defined]  # noqa: F821
    """DI factory for ThesisTimelineService (readmodel, read-only)."""
    from src.readmodel.timeline_service import ThesisTimelineService

    return ThesisTimelineService(session=session)


async def get_decision_service(
    session: AsyncSession = Depends(get_db),
    quote_svc: object = Depends(get_quote_service),
) -> "DecisionService":  # type: ignore[name-defined]  # noqa: F821
    """DI factory for DecisionService.

    ReplayAgent is injected so analyze_decision() can be called directly
    from the API without needing the scheduler.
    """
    from src.thesis.decision_service import DecisionService

    return DecisionService(
        session=session,
        quote_service=quote_svc,
        replay_agent=_get_replay_agent(),
    )


async def get_lesson_service(
    session: AsyncSession = Depends(get_db),
) -> "LessonService":  # type: ignore[name-defined]  # noqa: F821
    """DI factory for LessonService (read-only)."""
    from src.thesis.lesson_service import LessonService

    return LessonService(session=session)
