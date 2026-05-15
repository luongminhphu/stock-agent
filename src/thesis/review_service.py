"""
Thesis review service — orchestrates AI review flow.

Owner: thesis segment.
This is the ONLY place that calls ThesisReviewAgent.
Bot and API call this; they never call the agent directly.

Flow:
    1. Load thesis + assumptions + catalysts from DB (via ThesisRepository)
    2. Optionally fetch current price (injected, not fetched internally)
    3. Call ThesisReviewAgent.review() → ThesisReviewOutput
    4. Persist ThesisReview ORM record
    5. Auto-apply AI recommendations (ACCEPTED) → update assumption/catalyst status
    6. Reload thesis (fresh) → recompute full score with breakdown → persist
    7. Clamp score delta to MAX_SCORE_DELTA_PER_REVIEW to avoid single-event score spikes
    8. Persist ThesisSnapshot with score_breakdown (JSON) for conviction timeline
    9. Return ThesisReview
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.agents.thesis_review import ThesisReviewAgent
from src.ai.schemas import ThesisReviewOutput
from src.platform.logging import get_logger
from src.thesis.models import (
    AssumptionStatus,
    CatalystStatus,
    RecommendationStatus,
    ReviewRecommendation,
    ReviewVerdict,
    Thesis,
    ThesisReview,
    ThesisSnapshot,
    ThesisStatus,
)
from src.thesis.repository import ThesisRepository
from src.thesis.scoring_service import ScoringService
from src.thesis.service import ThesisNotFoundError

logger = get_logger(__name__)

# Wave 2: Maximum score change allowed in a single review cycle.
# Prevents a single INVALID assumption flip from causing a score spike > 20 pts.
# Tune upward only if thesis with many assumptions shows unnaturally flat curves.
MAX_SCORE_DELTA_PER_REVIEW: float = 20.0


class QuoteReader(Protocol):
    """Minimal quote interface required by ReviewService.

    Any object with a compatible get_quote() method satisfies this contract.
    Keeps ReviewService loosely coupled from the market segment.
    """

    async def get_quote(self, ticker: str): ...  # noqa: D102


class ReviewNotAllowedError(Exception):
    """Raised when a review is attempted on a non-active thesis."""


class ReviewService:
    """Orchestrates AI-powered thesis reviews.

    Dependencies injected at construction:
        session        — AsyncSession (per-request)
        agent          — ThesisReviewAgent (singleton from bootstrap)
        quote_service  — optional QuoteReader for live price enrichment
    """

    def __init__(
        self,
        session: AsyncSession,
        agent: ThesisReviewAgent,
        quote_service: QuoteReader | None = None,
    ) -> None:
        self._session = session  # stored for memory log pass-through
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

        if current_price is None and self._quote_service is not None:
            try:
                quote = await self._quote_service.get_quote(thesis.ticker)
                current_price = quote.price
            except Exception as exc:
                logger.warning(
                    "review_service.price_fetch_failed",
                    ticker=thesis.ticker,
                    error=str(exc),
                )

        assumptions_ctx = [
            {"id": a.id, "description": a.description}
            for a in thesis.assumptions
            if a.status != AssumptionStatus.INVALID
        ]
        pending_catalysts_ctx = [
            {"id": c.id, "description": c.description}
            for c in thesis.catalysts
            if c.status == CatalystStatus.PENDING
        ]
        triggered_catalysts_ctx = [
            {"id": c.id, "description": c.description}
            for c in thesis.catalysts
            if c.status == CatalystStatus.TRIGGERED
        ]

        logger.info(
            "review_service.calling_agent",
            thesis_id=thesis_id,
            ticker=thesis.ticker,
            assumptions_count=len(assumptions_ctx),
            pending_catalysts_count=len(pending_catalysts_ctx),
            triggered_catalysts_count=len(triggered_catalysts_ctx),
        )

        output: ThesisReviewOutput = await self._agent.review(
            ticker=thesis.ticker,
            thesis_title=thesis.title,
            thesis_summary=thesis.summary or "",
            assumptions_with_ids=assumptions_ctx,
            catalysts_with_ids=pending_catalysts_ctx,
            triggered_catalysts_with_ids=triggered_catalysts_ctx,
            current_price=current_price,
            entry_price=thesis.entry_price,
            target_price=thesis.target_price,
            # Memory wiring — pass session + identifiers for episodic log
            session=self._session,
            user_id=str(user_id),
            thesis_id=thesis.id,
            trigger="thesis_review",
        )

        review = await self._persist_review(thesis, output, current_price)
        logger.info(
            "review_service.done",
            thesis_id=thesis_id,
            verdict=review.verdict,
            confidence=review.confidence,
            recommendation_count=(
                len(output.assumption_recommendations) + len(output.catalyst_recommendations)
            ),
        )
        return review

    async def review_stale_theses(
        self,
        user_id: str,
        stale_days: int = 3,
    ) -> list[ThesisReview]:
        """Trigger AI review cho tất cả ACTIVE thesis chưa được review > stale_days ngày.

        Chạy mỗi ngày lúc 08:30 ICT bởi ThesisMaintenanceScheduler, sau bước
        auto_expire_overdue_catalysts. Loop tuần tự (không asyncio.gather) để
        tránh rate limit AI. Lỗi từng thesis được log và skip — không block các
        thesis còn lại.

        Args:
            user_id:    User sở hữu các thesis cần review.
            stale_days: Số ngày không có review trước khi cói là stale. Default: 3.

        Returns:
            List ThesisReview vừa tạo (chỉ các thesis đã review thành công).
        """
        stale = await self._repo.list_stale_theses(user_id, stale_days=stale_days)
        if not stale:
            logger.info(
                "review_service.stale_review.nothing_to_do",
                user_id=user_id,
                stale_days=stale_days,
            )
            return []

        logger.info(
            "review_service.stale_review.start",
            user_id=user_id,
            stale_days=stale_days,
            thesis_count=len(stale),
            thesis_ids=[t.id for t in stale],
        )

        results: list[ThesisReview] = []
        for thesis in stale:
            try:
                review = await self.review_thesis(
                    thesis_id=thesis.id,
                    user_id=user_id,
                )
                results.append(review)
                logger.info(
                    "review_service.stale_review.thesis_done",
                    thesis_id=thesis.id,
                    ticker=thesis.ticker,
                    verdict=review.verdict,
                )
            except Exception as exc:
                logger.warning(
                    "review_service.stale_review.thesis_failed",
                    thesis_id=thesis.id,
                    ticker=thesis.ticker,
                    error=str(exc),
                )

        logger.info(
            "review_service.stale_review.done",
            reviewed=len(results),
            skipped=len(stale) - len(results),
        )
        return results

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

    async def list_pending_recommendations(
        self,
        thesis_id: int,
        user_id: str,
    ) -> list[ReviewRecommendation]:
        """Trả danh sách recommendations PENDING cho một thesis.

        Kể từ khi auto-apply được bật, method này luôn trả về [] vì
        tất cả recommendations được ACCEPTED ngay tại thời điểm Verify.
        Giữ lại để backward compat với bot/API cũ.
        """
        thesis = await self._repo.get_by_id(thesis_id)
        if thesis is None or thesis.user_id != user_id:
            raise ThesisNotFoundError(f"Thesis {thesis_id} not found for user {user_id}")
        return await self._repo.list_pending_recommendations(thesis_id)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _persist_review(
        self,
        thesis: Thesis,
        output: ThesisReviewOutput,
        reviewed_price: float | None,
    ) -> ThesisReview:
        """Map ThesisReviewOutput → ThesisReview ORM, auto-apply recommendations,
        reload thesis fresh, recompute full score with breakdown, persist snapshot.

        Wave 2: score delta is clamped to MAX_SCORE_DELTA_PER_REVIEW so a single
        assumption flip cannot cause an unnaturally large score spike on the
        conviction chart. The raw computed score is still stored in score_breakdown
        for auditability — only thesis.score (persisted) is clamped.

        Schema note: ThesisReviewOutput uses `overall_verdict` (not `verdict`)
        and `key_risks` (not `risk_signals`) and `summary` (not `reasoning`).
        `next_watch_items` is not a schema field — default to empty list.
        """
        review = ThesisReview(
            thesis_id=thesis.id,
            verdict=ReviewVerdict(output.overall_verdict.value),
            confidence=output.confidence,
            reasoning=output.summary,
            risk_signals=json.dumps(output.key_risks, ensure_ascii=False),
            next_watch_items=json.dumps([], ensure_ascii=False),
            reviewed_at=datetime.now(UTC),
            reviewed_price=reviewed_price,
        )
        await self._repo.save_review(review)

        await self._auto_apply_recommendations(thesis, review.id, output)

        fresh_thesis = await self._repo.get_by_id(thesis.id)
        if fresh_thesis is not None:
            raw_score, breakdown = self._scoring.compute_with_breakdown(fresh_thesis)

            # Wave 2: clamp score delta to avoid single-event spikes on the chart.
            prev_score: float = fresh_thesis.score if fresh_thesis.score is not None else raw_score
            delta = raw_score - prev_score
            delta_capped = abs(delta) > MAX_SCORE_DELTA_PER_REVIEW
            if delta_capped:
                direction = 1.0 if delta > 0 else -1.0
                new_score = round(prev_score + direction * MAX_SCORE_DELTA_PER_REVIEW, 2)
                logger.info(
                    "review_service.score_delta_capped",
                    thesis_id=thesis.id,
                    prev_score=prev_score,
                    raw_score=raw_score,
                    capped_score=new_score,
                    delta=round(delta, 2),
                    cap=MAX_SCORE_DELTA_PER_REVIEW,
                )
            else:
                new_score = raw_score

            if fresh_thesis.score != new_score:
                fresh_thesis.score = new_score
                await self._repo.save(fresh_thesis)
                logger.info(
                    "review_service.score_updated",
                    thesis_id=thesis.id,
                    score=new_score,
                    delta_capped=delta_capped,
                )

            snapshot = ThesisSnapshot(
                thesis_id=thesis.id,
                score=new_score,
                verdict=review.verdict,
                confidence=review.confidence,
                conviction_score=output.conviction_score,
                score_breakdown=json.dumps(breakdown, ensure_ascii=False),
                recorded_at=review.reviewed_at,
            )
            await self._repo.save_snapshot(snapshot)
            logger.info(
                "review_service.snapshot_saved",
                thesis_id=thesis.id,
                score=new_score,
                conviction_score=output.conviction_score,
                breakdown=breakdown,
                delta_capped=delta_capped,
            )

        return review

    async def _auto_apply_recommendations(
        self,
        thesis: Thesis,
        review_id: int,
        output: ThesisReviewOutput,
    ) -> None:
        """Auto-apply toàn bộ AI recommendations ngay tại thời điểm Verify.

        Schema alignment (ThesisReviewOutput current contract):
          AssumptionRecommendation: assumption_id, status, evidence, updated_text
          CatalystRecommendation:   catalyst_id,  status, notes, updated_timeline
        """
        now = datetime.now(UTC)
        assumptions_by_id = {a.id: a for a in thesis.assumptions}
        catalysts_by_id = {c.id: c for c in thesis.catalysts}
        recs: list[ReviewRecommendation] = []

        for rec in output.assumption_recommendations:
            target_id = rec.assumption_id
            target = assumptions_by_id.get(target_id)
            if target:
                try:
                    target.status = AssumptionStatus(rec.status.lower())
                    await self._repo.save_assumption(target)
                except ValueError:
                    logger.warning(
                        "review_service.auto_apply.invalid_assumption_status",
                        recommended_status=rec.status,
                        target_id=target_id,
                        review_id=review_id,
                    )
            else:
                logger.warning(
                    "review_service.auto_apply.missing_assumption",
                    target_id=target_id,
                    review_id=review_id,
                )
            recs.append(
                ReviewRecommendation(
                    review_id=review_id,
                    target_type="assumption",
                    target_id=target_id,
                    target_description=rec.updated_text or rec.evidence,
                    recommended_status=rec.status,
                    reason=rec.evidence,
                    status=RecommendationStatus.ACCEPTED,
                    acted_at=now,
                )
            )

        for rec in output.catalyst_recommendations:
            target_id = rec.catalyst_id
            target = catalysts_by_id.get(target_id)
            if target:
                try:
                    target.status = CatalystStatus(rec.status.lower())
                    await self._repo.save_catalyst(target)
                except ValueError:
                    logger.warning(
                        "review_service.auto_apply.invalid_catalyst_status",
                        recommended_status=rec.status,
                        target_id=target_id,
                        review_id=review_id,
                    )
            else:
                logger.warning(
                    "review_service.auto_apply.missing_catalyst",
                    target_id=target_id,
                    review_id=review_id,
                )
            recs.append(
                ReviewRecommendation(
                    review_id=review_id,
                    target_type="catalyst",
                    target_id=target_id,
                    target_description=rec.updated_timeline or rec.notes,
                    recommended_status=rec.status,
                    reason=rec.notes,
                    status=RecommendationStatus.ACCEPTED,
                    acted_at=now,
                )
            )

        if recs:
            await self._repo.save_recommendations(recs)
            logger.info(
                "review_service.auto_apply.done",
                review_id=review_id,
                count=len(recs),
            )
