"""Thesis review service — orchestrates AI review flow.

Owner: thesis segment.
This is the ONLY place that calls ThesisReviewAgent.
Bot and API call this; they never call the agent directly.

Flow:
    1. Load thesis + assumptions + catalysts from DB (via ThesisRepository)
    2. Optionally fetch current price (injected, not fetched internally)
    3. Load previous review verdict for verdict_flip detection (W5C)
    4. Call ThesisReviewAgent.review() → ThesisReviewOutput
    5. Persist ThesisReview ORM record
    6. Log verdict_flip event to memory if verdict changed (W5C)
    7. React: ReviewOutcomeReactor mutates WatchlistItem + creates alerts
       (same session — committed together with review)
    8. Auto-apply AI recommendations (ACCEPTED) → update assumption/catalyst status
       Guard: assumptions already INVALID are skipped — only manual override can restore.
    9. Reload thesis (fresh) → recompute full score with breakdown → persist
   10. Clamp score delta to MAX_SCORE_DELTA_PER_REVIEW to avoid single-event score spikes
   11. Persist ThesisSnapshot with score_breakdown (JSON) for conviction timeline
   12. Return ThesisReview

Wave C (portfolio-priority sort):
    review_stale_theses() sorts the stale list so that tickers with high portfolio
    exposure (weight_pct > 10%) are reviewed first. This ensures that if the AI
    rate-limit or time budget is hit, the most risk-relevant theses are covered.
    Falls back to original DB order on any error — non-fatal, no schema change.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol

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

# W5A: assumption statuses that warrant an invalidation memory event.
_INVALIDATED_ASSUMPTION_STATUSES = frozenset({"invalidated", "invalid"})

# W5A: catalyst statuses that warrant a cancellation memory event.
_CANCELLED_CATALYST_STATUSES = frozenset({"cancelled", "canceled"})

# Wave C: portfolio exposure threshold — tickers above this weight are
# reviewed first in review_stale_theses() to prioritise risk-weighted review.
_HIGH_EXPOSURE_THRESHOLD_PCT: float = 10.0


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
        session         — AsyncSession (per-request)
        agent           — ThesisReviewAgent (singleton from bootstrap)
        quote_service   — optional QuoteReader for live price enrichment
        session_factory — optional async session factory; when provided,
                          ReviewOutcomeReactor runs after each review to
                          mutate watchlist priority/note and create alerts.
    """

    def __init__(
        self,
        session: AsyncSession,
        agent: ThesisReviewAgent,
        quote_service: QuoteReader | None = None,
        session_factory: Any | None = None,
    ) -> None:
        self._session = session
        self._repo = ThesisRepository(session)
        self._agent = agent
        self._quote_service = quote_service
        self._session_factory = session_factory
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

        # W5C: capture previous verdict BEFORE calling the agent so we can
        # detect a direction change after the new review is persisted.
        prev_review = await self._repo.get_latest_review(thesis_id)
        prev_verdict: ReviewVerdict | None = prev_review.verdict if prev_review else None

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
            prev_verdict=prev_verdict.value if prev_verdict else None,
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

        review = await self._persist_review(
            thesis,
            output,
            current_price,
            user_id=str(user_id),
            prev_verdict=prev_verdict,
        )
        logger.info(
            "review_service.done",
            thesis_id=thesis_id,
            verdict=review.verdict,
            confidence=review.confidence,
            verdict_flipped=(
                prev_verdict is not None and review.verdict != prev_verdict
            ),
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

        Wave C: sorts stale theses so that tickers with portfolio exposure
        weight_pct > 10% are reviewed first. Falls back to original DB order
        on any error — non-fatal, no schema migration needed.

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

        # Wave C: sort by portfolio exposure — high-exposure tickers reviewed first.
        # This ensures that if AI rate-limit or time budget is exhausted, the
        # most risk-relevant theses have already been covered.
        stale = await self._sort_by_portfolio_exposure(stale, user_id)

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

    async def _sort_by_portfolio_exposure(
        self,
        theses: list[Thesis],
        user_id: str,
    ) -> list[Thesis]:
        """Sort theses so high-exposure tickers come first.

        Wave C: uses PortfolioQueryAdapter singleton from bootstrap.
        Tickers with weight_pct > _HIGH_EXPOSURE_THRESHOLD_PCT get priority=0
        (sorted first); others get priority=1.

        Falls back to original order on any error so ThesisMaintenanceScheduler
        is never blocked by a portfolio read failure.
        """
        try:
            from src.platform.bootstrap import get_portfolio_query_adapter  # noqa: PLC0415

            adapter = get_portfolio_query_adapter()
            if adapter is None:
                return theses

            holdings = await adapter.get_holdings(user_id=user_id)  # type: ignore[union-attr]
            high_exposure: set[str] = {
                h.ticker
                for h in holdings
                if getattr(h, "weight_pct", 0) > _HIGH_EXPOSURE_THRESHOLD_PCT
            }

            if not high_exposure:
                return theses

            sorted_theses = sorted(
                theses,
                key=lambda t: (0 if t.ticker in high_exposure else 1),
            )

            logger.info(
                "review_service.stale_review.portfolio_sort_applied",
                user_id=user_id,
                high_exposure_tickers=sorted(high_exposure),
                prioritised=[
                    t.ticker for t in sorted_theses if t.ticker in high_exposure
                ],
            )
            return sorted_theses

        except Exception as exc:
            logger.warning(
                "review_service.stale_review.portfolio_sort_failed",
                user_id=user_id,
                error=str(exc),
                fallback="original_db_order",
            )
            return theses

    async def _persist_review(
        self,
        thesis: Thesis,
        output: ThesisReviewOutput,
        reviewed_price: float | None,
        user_id: str = "",
        prev_verdict: ReviewVerdict | None = None,
    ) -> ThesisReview:
        """Map ThesisReviewOutput → ThesisReview ORM, auto-apply recommendations,
        reload thesis fresh, recompute full score with breakdown, persist snapshot.

        Wave 2: score delta is clamped to MAX_SCORE_DELTA_PER_REVIEW so a single
        assumption flip cannot cause an unnaturally large score spike on the
        conviction chart. The raw computed score is still stored in score_breakdown
        for auditability — only thesis.score (persisted) is clamped.

        Wave 3: ReviewOutcomeReactor runs in the same session after save_review()
        to mutate WatchlistItem priority/note and create THESIS_TRIGGER alerts.
        Reactor failure is non-fatal — logged and skipped, review is still returned.

        W5A: user_id forwarded to _auto_apply_recommendations() so invalidation
        events can be written to episodic memory (non-fatal).

        W5C: prev_verdict compared to new verdict immediately after save_review().
        A flip (e.g. BULLISH → BEARISH) writes a verdict_flip memory event so
        the agent can reason about direction changes on the next review.
        First-ever review (prev_verdict=None) is skipped — no flip to record.

        Schema note: ThesisReviewOutput uses `overall_verdict` (not `verdict`)
        and `key_risks` (not `risk_signals`) and `summary` (not `reasoning`).
        `next_watch_items` is not a schema field — default to empty list.
        """
        review_ts = datetime.now(UTC)
        review = ThesisReview(
            thesis_id=thesis.id,
            verdict=ReviewVerdict(output.overall_verdict.value),
            confidence=output.confidence,
            reasoning=output.summary,
            risk_signals=json.dumps(output.key_risks, ensure_ascii=False),
            next_watch_items=json.dumps([], ensure_ascii=False),
            reviewed_at=review_ts,
            reviewed_price=reviewed_price,
        )
        await self._repo.save_review(review)

        # Propagate last_reviewed_at up to Thesis row so:
        #  - snapshot stale detection reads from column (no subquery)
        #  - Wave 4 dedup guard in ThesisJudgeAgent.run_batch() fires correctly
        thesis.last_reviewed_at = review_ts  # type: ignore[assignment]

        # W5C: log verdict_flip event if direction changed from previous review.
        # Skipped on first-ever review (prev_verdict is None).
        if prev_verdict is not None and review.verdict != prev_verdict:
            await self._log_invalidation_event(
                user_id=user_id,
                ticker=thesis.ticker,
                thesis_id=thesis.id,
                agent_type="verdict_flip",
                description=f"{prev_verdict.value.upper()} → {review.verdict.value.upper()}",
                evidence=output.summary or "",
                target_id=review.id,
            )
            logger.info(
                "review_service.verdict_flip",
                thesis_id=thesis.id,
                ticker=thesis.ticker,
                from_verdict=prev_verdict.value,
                to_verdict=review.verdict.value,
                confidence=review.confidence,
            )

        # Wave 3: react to review outcome — mutate watchlist in same session.
        await self._run_watchlist_reactor(review.id)

        await self._auto_apply_recommendations(thesis, review.id, output, user_id=user_id)

        fresh_thesis = await self._repo.get_by_id(thesis.id)
        if fresh_thesis is not None:
            raw_score, breakdown = self._scoring.compute_with_breakdown(fresh_thesis)

            # Wave 2: clamp score delta to avoid single-event spikes on the chart.
            # Fallback to 0.0 (not raw_score) so the cap correctly triggers on the
            # first review when thesis.score is None — delta = raw_score - 0.
            prev_score: float = fresh_thesis.score if fresh_thesis.score is not None else 0.0
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

    async def _run_watchlist_reactor(self, review_id: int) -> None:
        """Run ReviewOutcomeReactor in the current session (non-fatal).

        Skipped silently when session_factory was not injected — backward
        compat for callers that construct ReviewService without it.
        """
        if self._session_factory is None:
            logger.debug(
                "review_service.reactor_skipped.no_session_factory",
                review_id=review_id,
            )
            return

        try:
            # Local import — keeps thesis segment boundary clean
            # (watchlist must not be imported at module level from thesis)
            from src.watchlist.review_outcome_reactor import ReviewOutcomeReactor  # noqa: PLC0415

            reactor = ReviewOutcomeReactor(session_factory=self._session_factory)
            await reactor.react_in_session(self._session, review_id)
            logger.info(
                "review_service.reactor_done",
                review_id=review_id,
            )
        except Exception as exc:
            # Non-fatal: watchlist mutation failure must not roll back the review.
            logger.warning(
                "review_service.reactor_failed",
                review_id=review_id,
                error=str(exc),
            )

    async def _auto_apply_recommendations(
        self,
        thesis: Thesis,
        review_id: int,
        output: ThesisReviewOutput,
        user_id: str = "",
    ) -> None:
        """Auto-apply toàn bộ AI recommendations ngay tại thời điểm Verify.

        Guard (Issue A): Assumptions đang ở trạng thái INVALID bị skip —
        chỉ manual override mới được phép restore về VALID/NEEDS_MONITORING.
        Điều này ngăn AI inconsistency tạo ra vòng flip INVALID ↔ VALID.

        W5A: Khi assumption bị INVALIDATED hoặc catalyst bị CANCELLED, ghi một
        AIInteractionLog event riêng vào episodic memory (non-fatal, isolated
        session). Lần review sau ThesisReviewAgent sẽ đọc được lý do invalidation
        từ ai_risk_signals thay vì chỉ thấy assumption đã biến mất khỏi danh sách.

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
                # Guard: skip auto-apply if assumption is already INVALID.
                # Restoring an INVALID assumption requires explicit manual action.
                if target.status == AssumptionStatus.INVALID:
                    logger.info(
                        "review_service.auto_apply.assumption_invalid_skipped",
                        target_id=target_id,
                        review_id=review_id,
                        recommended_status=rec.status,
                    )
                else:
                    try:
                        new_status = AssumptionStatus(rec.status.lower())
                        target.status = new_status
                        await self._repo.save_assumption(target)

                        # W5A: log invalidation event to episodic memory so the
                        # agent has context on WHY this assumption was removed next time.
                        if rec.status.lower() in _INVALIDATED_ASSUMPTION_STATUSES:
                            await self._log_invalidation_event(
                                user_id=user_id,
                                ticker=thesis.ticker,
                                thesis_id=thesis.id,
                                agent_type="assumption_invalidated",
                                description=target.description,
                                evidence=rec.evidence or "",
                                target_id=target_id,
                            )
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
                    new_status = CatalystStatus(rec.status.lower())
                    target.status = new_status
                    await self._repo.save_catalyst(target)

                    # W5A: log cancellation event to episodic memory so the
                    # agent knows this catalyst failed and why.
                    if rec.status.lower() in _CANCELLED_CATALYST_STATUSES:
                        await self._log_invalidation_event(
                            user_id=user_id,
                            ticker=thesis.ticker,
                            thesis_id=thesis.id,
                            agent_type="catalyst_cancelled",
                            description=target.description,
                            evidence=rec.notes or "",
                            target_id=target_id,
                        )
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

    async def _log_invalidation_event(
        self,
        user_id: str,
        ticker: str,
        thesis_id: int,
        agent_type: str,
        description: str,
        evidence: str,
        target_id: int,
    ) -> None:
        """Write an invalidation/cancellation/flip event to episodic memory.

        W5A: Called after an assumption is INVALIDATED or a catalyst is CANCELLED.
        W5C: Called after a verdict_flip is detected (prev_verdict != new verdict).

        Gives ThesisReviewAgent access to WHY an assumption/catalyst disappeared
        from the active list, or WHY the overall verdict changed direction, on
        subsequent reviews.

        Non-fatal: exceptions are swallowed. Memory writes must never block the
        review transaction.

        Args:
            user_id:     Owner of the thesis.
            ticker:      Stock ticker for episode scoping.
            thesis_id:   FK for thesis-scoped memory queries.
            agent_type:  "assumption_invalidated", "catalyst_cancelled", or "verdict_flip".
            description: Human-readable summary of WHAT changed.
            evidence:    AI-provided reason / output.summary for the change.
            target_id:   Numeric ID of the subject (assumption, catalyst, or review).
        """
        if not user_id:
            return
        try:
            from src.ai.memory.memory_service import InteractionEntry, MemoryService  # noqa: PLC0415

            await MemoryService.log_interaction(
                session=self._session,
                entry=InteractionEntry(
                    user_id=user_id,
                    agent_type=agent_type,
                    trigger="auto_apply",
                    tickers=[ticker],
                    thesis_id=thesis_id,
                    ai_verdict=agent_type.upper(),
                    ai_key_points=f"[ID {target_id}] {description}",
                    ai_risk_signals=evidence,
                ),
            )
            logger.debug(
                "review_service.invalidation_event_logged",
                agent_type=agent_type,
                ticker=ticker,
                thesis_id=thesis_id,
                target_id=target_id,
            )
        except Exception as exc:
            logger.warning(
                "review_service.invalidation_event_log_failed",
                agent_type=agent_type,
                ticker=ticker,
                thesis_id=thesis_id,
                target_id=target_id,
                error=str(exc),
            )
