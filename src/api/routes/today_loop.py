"""GET /api/v1/today-loop — Daily investor intelligence summary.

Owner: api segment — thin adapter only.
Delegates 100% to readmodel.today_loop_digest.build_today_loop_digest (Wave F3).
No AI calls. No mutation. Pure read + aggregate.

Endpoints:
    GET /api/v1/today-loop            — single-user (owner_user_id from .env)
    GET /api/v1/today-loop/{user_id}  — multi-user

Response shape:
    attention_items   — urgent tasks (alerts, stop-loss, overdue review, catalyst)
    top_signals       — top-N ranked signals (by strength) from recent signal events
    brief_summary     — latest morning brief narrative + metadata
    thesis_digest     — active theses flagged: low_conviction | overdue_review
    market_mood       — scan-level bias from latest WatchlistScan JSON
    generated_at      — ISO UTC timestamp of this aggregation
    stale_sources     — list of sources that failed (partial results ok, no 500)
    meta              — quick-count summary for UI badges

Design notes: xem readmodel/today_loop_digest.py (nguồn, rule cờ, stale_sources).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import default_user_id, get_db
from src.readmodel.today_loop_digest import build_today_loop_digest

router = APIRouter(prefix="/today-loop", tags=["today-loop"])


@router.get("/{user_id}")
async def get_today_loop(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    enrich_prices: Annotated[
        bool,
        Query(
            description="Fetch live prices from QuoteService for stop_loss proximity check and thesis P&L."
        ),
    ] = True,
    attention_limit: Annotated[
        int,
        Query(ge=1, le=50, description="Max attention items returned."),
    ] = 20,
    signal_limit: Annotated[
        int,
        Query(ge=1, le=50, description="Max signal items returned (sorted by strength desc)."),
    ] = 10,
) -> dict[str, Any]:
    """Daily investor intelligence loop cho một user cụ thể.

    Aggregates 5 sources in one call:
      1. attention_items  — urgent tasks (alerts, stop-loss, overdue review, upcoming catalyst)
      2. top_signals      — ranked signals from recent 7-day signal events
      3. brief_summary    — latest morning brief narrative
      4. thesis_digest    — active theses flagged for action
      5. market_mood      — scan-level market bias from latest WatchlistScan

    Always returns 200. stale_sources lists any source that failed.
    No AI calls. No mutation. Pure read + aggregate.
    """
    return await build_today_loop_digest(
        session=session,
        user_id=user_id,
        enrich_prices=enrich_prices,
        attention_limit=attention_limit,
        signal_limit=signal_limit,
    )


@router.get("")
async def get_today_loop_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    enrich_prices: Annotated[
        bool,
        Query(
            description="Fetch live prices from QuoteService for stop_loss proximity check and thesis P&L."
        ),
    ] = True,
    attention_limit: Annotated[
        int,
        Query(ge=1, le=50, description="Max attention items returned."),
    ] = 20,
    signal_limit: Annotated[
        int,
        Query(ge=1, le=50, description="Max signal items returned (sorted by strength desc)."),
    ] = 10,
) -> dict[str, Any]:
    """Single-user alias — dùng owner_user_id từ .env."""
    return await build_today_loop_digest(
        session=session,
        user_id=default_user_id(),
        enrich_prices=enrich_prices,
        attention_limit=attention_limit,
        signal_limit=signal_limit,
    )
