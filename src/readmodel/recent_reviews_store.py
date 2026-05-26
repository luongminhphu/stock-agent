"""RecentReviewsStore — read-optimised query for recent AI thesis reviews.

Owner: readmodel segment.
Purpose: surface ThesisReview records produced by the SignalEngine loop
         so that bot commands, API routes, and briefing context can access
         AI judge output without querying thesis domain models directly.

Usage::

    store = RecentReviewsStore(session_factory=AsyncSessionLocal)
    result = await store.get_recent(
        user_id="123",
        since_hours=24,      # default 24 — last 24 hours
        limit=20,            # default 20
        ticker=None,         # optional ticker filter
    )
    # result: RecentReviewsResponse

Query design:
  - Single JOIN: thesis_reviews ← theses (for ticker + title)
  - Filtered by theses.user_id to scope to one investor
  - risk_signals / next_watch_items stored as newline-delimited Text,
    parsed into list[str] here so callers never need to know storage detail
  - No lazy loads — fully async-safe
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.readmodel.schemas import RecentReviewRow, RecentReviewsResponse

logger = get_logger(__name__)


def _parse_text_list(raw: str | None) -> list[str]:
    """Parse risk_signals / next_watch_items from stored Text.

    Supports two formats written by ThesisReviewAgent:
    1. JSON array string: '["item1", "item2"]'
    2. Newline-delimited string: 'item1\nitem2'

    Returns empty list on any parse failure.
    """
    if not raw:
        return []
    stripped = raw.strip()
    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return [str(x) for x in parsed if x]
        except (json.JSONDecodeError, ValueError):
            pass
    # fallback: newline-delimited
    return [line.strip() for line in stripped.splitlines() if line.strip()]


class RecentReviewsStore:
    """Async read store for recent ThesisReview records scoped to one user.

    Stateless — safe to instantiate per request or as a singleton.
    """

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def get_recent(
        self,
        user_id: str,
        since_hours: int = 24,
        limit: int = 20,
        ticker: str | None = None,
    ) -> RecentReviewsResponse:
        """Return recent AI reviews for *user_id* within *since_hours*.

        Args:
            user_id:     Investor user_id — scopes to their theses only.
            since_hours: Look-back window in hours (default 24).
            limit:       Max rows returned (default 20, max capped at 100).
            ticker:      Optional single-ticker filter (case-insensitive).

        Returns:
            RecentReviewsResponse with rows sorted newest-first.
        """
        limit = min(limit, 100)
        since_dt = datetime.now(tz=timezone.utc) - timedelta(hours=since_hours)

        try:
            async with self._session_factory() as session:
                rows = await self._query(
                    session=session,
                    user_id=user_id,
                    since_dt=since_dt,
                    limit=limit,
                    ticker=ticker,
                )
        except Exception as exc:
            logger.exception(
                "recent_reviews_store.query_failed",
                user_id=user_id,
                error=str(exc),
            )
            rows = []

        return RecentReviewsResponse(
            user_id=user_id,
            since_hours=since_hours,
            ticker_filter=ticker,
            generated_at=datetime.now(tz=timezone.utc),
            rows=rows,
            total=len(rows),
        )

    # ── internal ──────────────────────────────────────────────────────────

    async def _query(
        self,
        session: AsyncSession,
        user_id: str,
        since_dt: datetime,
        limit: int,
        ticker: str | None,
    ) -> list[RecentReviewRow]:
        """Execute the joined query and map rows to RecentReviewRow."""
        # Import ORM models here to keep readmodel segment boundary clean
        # (no top-level thesis model import in readmodel module)
        from src.thesis.models import Thesis, ThesisReview  # noqa: PLC0415

        stmt = (
            select(
                ThesisReview.id,
                ThesisReview.thesis_id,
                ThesisReview.verdict,
                ThesisReview.confidence,
                ThesisReview.reasoning,
                ThesisReview.risk_signals,
                ThesisReview.next_watch_items,
                ThesisReview.reviewed_at,
                ThesisReview.reviewed_price,
                ThesisReview.summary,
                Thesis.ticker,
                Thesis.title,
                Thesis.status.label("thesis_status"),
            )
            .join(Thesis, ThesisReview.thesis_id == Thesis.id)
            .where(Thesis.user_id == user_id)
            .where(ThesisReview.reviewed_at >= since_dt)
            .order_by(ThesisReview.reviewed_at.desc())
            .limit(limit)
        )

        if ticker:
            stmt = stmt.where(Thesis.ticker == ticker.upper())

        result = await session.execute(stmt)
        raw_rows = result.fetchall()

        out: list[RecentReviewRow] = []
        for r in raw_rows:
            confidence_pct = round(float(r.confidence) * 100) if r.confidence else 0
            out.append(
                RecentReviewRow(
                    review_id=r.id,
                    thesis_id=r.thesis_id,
                    ticker=r.ticker,
                    thesis_title=r.title,
                    thesis_status=str(r.thesis_status.value) if hasattr(r.thesis_status, "value") else str(r.thesis_status),
                    verdict=str(r.verdict.value) if hasattr(r.verdict, "value") else str(r.verdict),
                    confidence=float(r.confidence),
                    confidence_pct=confidence_pct,
                    reasoning=r.reasoning,
                    summary=r.summary,
                    risk_signals=_parse_text_list(r.risk_signals),
                    next_watch_items=_parse_text_list(r.next_watch_items),
                    reviewed_at=r.reviewed_at,
                    reviewed_price=r.reviewed_price,
                )
            )

        logger.debug(
            "recent_reviews_store.query_done",
            user_id=user_id,
            since_hours=int((datetime.now(tz=timezone.utc) - since_dt).total_seconds() / 3600),
            rows=len(out),
        )
        return out
