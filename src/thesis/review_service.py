"""Thesis review service — orchestrates AI review flow.

Owner: thesis segment.
This is the ONLY place that calls ThesisReviewAgent.
Bot and API call this; they never call the agent directly.

Flow:
    1. Load thesis + assumptions + catalysts from DB (via ThesisRepository)
    2. Optionally fetch current price (injected, not fetched internally)
    3. Call ThesisReviewAgent.review() → ThesisReviewOutput
    4. Persist ThesisReview ORM record
    5. Reload thesis (with new review) → recompute score → persist
    6. Return ThesisReview
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.agents.thesis_review import ThesisReviewAgent
from src.ai.schemas import ThesisReviewOutput
from src.platform.logging import get_logger
from src.thesis.models import (
    AssumptionStatus,
    CatalystStatus,
    ReviewVerdict,
    Thesis,
    ThesisReview,
    ThesisStatus,
)
from src.thesis.repository import ThesisRepository
from src.thesis.scoring_service import ScoringService
from src.thesis.service import ThesisNotFoundError

logger = get_logger(__name__)


class ReviewNotAllowedError(Exception):
    """Raised when a review is attempted on a non-active thesis."""


class ReviewService:
    """Orchestrates AI-powered thesis reviews.

    Dependencies injected at construction:
        session        — AsyncSession (per-request)
        agent          — ThesisReviewAgent (singleton from bootstrap)
        quote_service  — optional QuoteService for live price enrichment
    """

    def __init__(
        self,
        session: AsyncSession,
        agent: ThesisReviewAgent,
        quote_service: object | None = None,  # QuoteService | None, avoid circular import
    ) -> None:
        self._repo = ThesisRepository(session)
        self._agent = agent
        self._quote_service = quote_service
        self._scoring = ScoringService()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def review_thesis(
        self,
        thesis_id: int,
        user_id: str,
        current_price: float | None = None,
    ) -> ThesisReview:
        """Run an AI review on a thesis and persist the result.

        Args:
            thesis_id:     Thesis to review.
            user_id:       Owner of the thesis — validates ownership.
            current_price: Override live price. If None, fetched from
                           QuoteService if available.

        Returns:
            Persisted ThesisReview ORM instance.

        Raises:
            ThesisNotFoundError:   thesis_id not found or not owned by user_id.
            ReviewNotAllowedError: thesis is not ACTIVE.
            PerplexityError:       AI call failed after retries.
            ValueError:            AI response could not be parsed.
        """
        thesis = await self._repo.get_by_id(thesis_id)
        if thesis is None or thesis.user_id != user_id:
            raise ThesisNotFoundError(f"Thesis {thesis_id} not found for user {user_id}")

        if thesis.status != ThesisStatus.ACTIVE:
            raise ReviewNotAllowedError(
                f"Thesis {thesis_id} is {thesis.status} — only ACTIVE theses can be reviewed."
            )

        # Enrich with live price if not provided
        if current_price is None and self._quote_service is not None:
            try:
                quote = await self._quote_service.get_quote(thesis.ticker)  # type: ignore[attr-defined]
                current_price = quote.price
            except Exception as exc:
                logger.warning(
                    "review_service.price_fetch_failed",
                    ticker=thesis.ticker,
                    error=str(exc),
                )

        # Build context lists
        assumptions = [
            a.description for a in thesis.assumptions if a.status != AssumptionStatus.INVALID
        ]

        # Tách rõ PENDING (sắp tới) vs TRIGGERED (đã kích hoạt).
        # EXPIRED/CANCELLED bị loại — không còn relevance với AI review.
        pending_catalysts = [
            c.description
            for c in thesis.catalysts
            if c.status == CatalystStatus.PENDING
        ]
        triggered_catalysts = [
            c.description
            for c in thesis.catalysts
            if c.status == CatalystStatus.TRIGGERED
        ]

        logger.info(
            "review_service.calling_agent",
            thesis_id=thesis_id,
            ticker=thesis.ticker,
            assumptions_count=len(assumptions),
            pending_catalysts_count=len(pending_catalysts),
            triggered_catalysts_count=len(triggered_catalysts),
        )

        output: ThesisReviewOutput = await self._agent.review(
            ticker=thesis.ticker,
            thesis_title=thesis.title,
            thesis_summary=thesis.summary or "",
            assumptions=assumptions,
            catalysts=pending_catalysts,
            triggered_catalysts=triggered_catalysts,
            current_price=current_price,
            entry_price=thesis.entry_price,
            target_price=thesis.target_price,
        )

        review = await self._persist_review(thesis, output, current_price)
        logger.info(
            "review_service.done",
            thesis_id=thesis_id,
            verdict=review.verdict,
            confidence=review.confidence,
        )
        return review

    async def list_reviews(
        self,
        thesis_id: int,
        user_id: str,
        limit: int = 10,
    ) -> list[ThesisReview]:
        """Return recent reviews for a thesis (validates ownership)."""
        thesis = await self._repo.get_by_id(thesis_id)
        if thesis is None or thesis.user_id != user_id:
            raise ThesisNotFoundError(f"Thesis {thesis_id} not found for user {user_id}")
        return await self._repo.list_reviews_by_thesis(thesis_id, limit=limit)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _persist_review(
        self,
        thesis: Thesis,
        output: ThesisReviewOutput,
        reviewed_price: float | None,
    ) -> ThesisReview:
        """Map ThesisReviewOutput → ThesisReview ORM, save, then recompute score.

        Score recompute flow:
            1. save_review (flush) → review gets a DB id.
            2. Reload thesis via get_by_id → session-fresh object with the
               new review already included in thesis.reviews (selectinload).
            3. ScoringService.compute(thesis) → new_score.
            4. Persist thesis.score only if it changed (avoids redundant flush).

        We reload instead of appending in-memory to avoid:
            - SQLAlchemy InvalidRequestError (appending a tracked object to a
              collection that already knows about it after flush).
            - Stale in-memory state if the session expired attributes on flush.
        """
        review = ThesisReview(
            thesis_id=thesis.id,
            verdict=ReviewVerdict(output.verdict.value),
            confidence=output.confidence,
            reasoning=output.reasoning,
            risk_signals=json.dumps(output.risk_signals, ensure_ascii=False),
            next_watch_items=json.dumps(output.next_watch_items, ensure_ascii=False),
            reviewed_at=datetime.now(timezone.utc),
            reviewed_price=reviewed_price,
        )
        await self._repo.save_review(review)

        # Reload thesis so thesis.reviews includes the new review just persisted.
        # get_by_id uses selectinload for assumptions, catalysts, reviews —
        # guarantees ScoringService sees the complete, up-to-date state.
        fresh_thesis = await self._repo.get_by_id(thesis.id)
        if fresh_thesis is not None:
            new_score = self._scoring.compute(fresh_thesis)
            if fresh_thesis.score != new_score:
                fresh_thesis.score = new_score
                await self._repo.save(fresh_thesis)
                logger.info(
                    "review_service.score_updated",
                    thesis_id=thesis.id,
                    score=new_score,
                )

        return review
