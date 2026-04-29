"""Thesis review service — orchestrates AI review flow.

Owner: thesis segment.
This is the ONLY place that calls ThesisReviewAgent.
Bot and API call this; they never call the agent directly.

Flow:
    1. Load thesis + assumptions + catalysts from DB (via ThesisRepository)
    2. Optionally fetch current price (injected, not fetched internally)
    3. Call ThesisReviewAgent.review() → ThesisReviewOutput
    4. Persist ThesisReview ORM record
    5. Auto-apply AI recommendations (ACCEPTED) → update assumption/catalyst status
    6. Reload thesis (fresh) → recompute full score → persist
    7. Return ThesisReview
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

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

        # Build context lists — truyền đủ id + description để AI có thể
        # populate AssumptionRecommendation.target_id chính xác.
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
            assumptions=[a["description"] for a in assumptions_ctx],
            assumptions_with_ids=assumptions_ctx,
            catalysts=[c["description"] for c in pending_catalysts_ctx],
            catalysts_with_ids=pending_catalysts_ctx,
            triggered_catalysts=[c["description"] for c in triggered_catalysts_ctx],
            triggered_catalysts_with_ids=triggered_catalysts_ctx,
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
            stale_days: Số ngày không có review trước khi coi là stale. Default: 3.

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

    async def apply_bulk_recommendations(
        self,
        thesis_id: int,
        user_id: str,
        applied_recommendation_ids: list[int],
        verdict: str | None = None,
        ai_confidence: float | None = None,
    ) -> None:
        """No-op kể từ khi auto-apply được bật.

        Recommendations được ACCEPTED ngay tại _persist_review → không còn
        PENDING nào để apply. Giữ lại để backward compat với bot/API cũ.
        """
        logger.info(
            "review_service.apply_bulk.skipped_auto_apply_enabled",
            thesis_id=thesis_id,
            requested_ids=applied_recommendation_ids,
        )

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
        reload thesis fresh, recompute full score.

        Auto-apply flow:
            1. save_review (flush) → review gets a DB id.
            2. _auto_apply_recommendations:
               - Update Assumption/Catalyst status in DB (save riêng từng object).
               - Insert ReviewRecommendation với status=ACCEPTED ngay lập tức.
            3. Reload thesis via get_by_id (populate_existing) → session-fresh object
               với assumption/catalyst status đã được update.
            4. ScoringService.compute(thesis) → new_score (tất cả 4 components fresh).
            5. Persist thesis.score only if it changed.
        """
        review = ThesisReview(
            thesis_id=thesis.id,
            verdict=ReviewVerdict(output.verdict.value),
            confidence=output.confidence,
            reasoning=output.reasoning,
            risk_signals=json.dumps(output.risk_signals, ensure_ascii=False),
            next_watch_items=json.dumps(output.next_watch_items, ensure_ascii=False),
            reviewed_at=datetime.now(UTC),
            reviewed_price=reviewed_price,
        )
        await self._repo.save_review(review)

        # Auto-apply: update assumption/catalyst status + insert recs as ACCEPTED.
        await self._auto_apply_recommendations(thesis, review.id, output)

        # Reload fresh — assumptions/catalysts đã được update, reviews đã có review mới.
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

    async def _auto_apply_recommendations(
        self,
        thesis: Thesis,
        review_id: int,
        output: ThesisReviewOutput,
    ) -> None:
        """Auto-apply toàn bộ AI recommendations ngay tại thời điểm Verify.

        - Update Assumption.status / Catalyst.status → persist riêng từng object.
        - Insert ReviewRecommendation với status=ACCEPTED và acted_at=now.
        - Không raise nếu target_id không tìm thấy — log warning và tiếp tục.
        """
        now = datetime.now(UTC)
        assumptions_by_id = {a.id: a for a in thesis.assumptions}
        catalysts_by_id = {c.id: c for c in thesis.catalysts}
        recs: list[ReviewRecommendation] = []

        for rec in output.assumption_recommendations:
            target = assumptions_by_id.get(rec.target_id)
            if target:
                try:
                    target.status = AssumptionStatus(rec.recommended_status.lower())
                    await self._repo.save_assumption(target)
                except ValueError:
                    logger.warning(
                        "review_service.auto_apply.invalid_assumption_status",
                        recommended_status=rec.recommended_status,
                        target_id=rec.target_id,
                        review_id=review_id,
                    )
            else:
                logger.warning(
                    "review_service.auto_apply.missing_assumption",
                    target_id=rec.target_id,
                    review_id=review_id,
                )
            recs.append(
                ReviewRecommendation(
                    review_id=review_id,
                    target_type="assumption",
                    target_id=rec.target_id,
                    target_description=rec.description,
                    recommended_status=rec.recommended_status,
                    reason=rec.reason,
                    status=RecommendationStatus.ACCEPTED,
                    acted_at=now,
                )
            )

        for rec in output.catalyst_recommendations:
            target = catalysts_by_id.get(rec.target_id)
            if target:
                try:
                    target.status = CatalystStatus(rec.recommended_status.lower())
                    await self._repo.save_catalyst(target)
                except ValueError:
                    logger.warning(
                        "review_service.auto_apply.invalid_catalyst_status",
                        recommended_status=rec.recommended_status,
                        target_id=rec.target_id,
                        review_id=review_id,
                    )
            else:
                logger.warning(
                    "review_service.auto_apply.missing_catalyst",
                    target_id=rec.target_id,
                    review_id=review_id,
                )
            recs.append(
                ReviewRecommendation(
                    review_id=review_id,
                    target_type="catalyst",
                    target_id=rec.target_id,
                    target_description=rec.description,
                    recommended_status=rec.recommended_status,
                    reason=rec.reason,
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
