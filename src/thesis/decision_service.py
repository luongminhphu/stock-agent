"""Decision service for Decision Replay & Learning Loop.

Owner: thesis segment.

Responsibilities:
- Persist immutable decision logs (BUY / SELL / HOLD / ADD / REDUCE).
- Freeze relevant thesis + market context at decision time.
- Evaluate realized outcome after a review horizon (30/90 days, configurable).
- Call ReplayAgent to produce personalized lessons.
- Persist AI-generated key_lesson and pattern_detected back to DecisionLog.
- After persisting a key_lesson, request an AI review of the linked thesis
  (lesson → thesis review loop closure).

Non-responsibilities:
- Does not send notifications.
- Does not own readmodel projections.
- Does not execute trades.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.prompts.replay import ReplayContext
from src.platform.logging import get_logger
from src.thesis.models import DecisionLog, Thesis
from src.thesis.repository import ThesisRepository

logger = get_logger(__name__)

_VALID_DECISION_TYPES = {"BUY", "SELL", "HOLD", "ADD", "REDUCE"}
_VALID_OUTCOME_VERDICTS = {"CORRECT", "INCORRECT", "MIXED"}
_DEFAULT_REVIEW_HORIZON_DAYS = 30


class DecisionNotFoundError(Exception):
    """Raised when a decision is not found or not owned by the requesting user."""


@dataclass
class DecisionReplayEnvelope:
    decision_id: int
    ticker: str
    outcome_verdict: str | None
    replay: Any | None


class DecisionService:
    def __init__(
        self,
        session: AsyncSession,
        quote_service: object | None = None,
        replay_agent: object | None = None,
    ) -> None:
        self._session = session
        self._repo = ThesisRepository(session)
        self._quote_service = quote_service
        self._replay_agent = replay_agent

    async def log_decision(
        self,
        *,
        thesis_id: int,
        user_id: str,
        decision_type: str,
        rationale: str,
        brief_summary: str | None = None,
        active_signal: str | None = None,
        review_horizon_days: int = _DEFAULT_REVIEW_HORIZON_DAYS,
    ) -> DecisionLog:
        """Persist one immutable decision with frozen context at decision time."""
        decision_type = decision_type.upper().strip()
        if decision_type not in _VALID_DECISION_TYPES:
            raise ValueError(f"Unsupported decision_type={decision_type}")

        thesis = await self._repo.get_by_id(thesis_id)
        if thesis is None:
            raise ValueError(f"Thesis #{thesis_id} not found")
        if str(thesis.user_id) != str(user_id):
            raise PermissionError(f"Thesis #{thesis_id} does not belong to this user")
        current_price = await self._safe_get_current_price(thesis.ticker)
        thesis_score = self._infer_current_thesis_score(thesis)
        thesis_health_score = self._infer_current_health_score(thesis)

        row = DecisionLog(
            thesis_id=thesis.id,
            user_id=user_id,
            ticker=thesis.ticker,
            decision_type=decision_type,
            decision_at=datetime.now(UTC),
            price_at_decision=current_price,
            thesis_score_at_decision=thesis_score,
            thesis_health_score_at_decision=thesis_health_score,
            active_signal=active_signal,
            brief_summary=brief_summary,
            rationale=rationale,
            review_horizon_days=review_horizon_days,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        logger.info(
            "decision.logged",
            decision_id=row.id,
            thesis_id=thesis.id,
            ticker=thesis.ticker,
            decision_type=decision_type,
        )
        return row

    async def list_decisions(
        self,
        user_id: str,
        *,
        evaluated_only: bool = False,
        ticker: str | None = None,
        limit: int = 50,
    ) -> list[DecisionLog]:
        """List decision logs for a user, newest first.

        Args:
            user_id:        Filter to this user's decisions only.
            evaluated_only: When True, return only decisions with a realized outcome.
            ticker:         Optional — narrow to one ticker symbol (uppercased).
            limit:          Max rows (1-200, default 50).

        Returns:
            List of DecisionLog rows ordered by decision_at DESC.
        """
        limit = min(max(limit, 1), 200)
        stmt = (
            select(DecisionLog)
            .where(DecisionLog.user_id == user_id)
            .order_by(DecisionLog.decision_at.desc())
            .limit(limit)
        )
        if evaluated_only:
            stmt = stmt.where(DecisionLog.outcome_evaluated_at.is_not(None))
        if ticker:
            stmt = stmt.where(DecisionLog.ticker == ticker.upper().strip())
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows)

    async def list_lessons(
        self,
        user_id: str,
        *,
        ticker: str | None = None,
        limit: int = 20,
    ) -> list[DecisionLog]:
        """Return decisions that have an AI-generated key_lesson, newest first."""
        limit = min(max(limit, 1), 50)
        stmt = (
            select(DecisionLog)
            .where(
                DecisionLog.user_id == user_id,
                DecisionLog.key_lesson.is_not(None),
            )
            .order_by(DecisionLog.decision_at.desc())
            .limit(limit)
        )
        if ticker:
            stmt = stmt.where(DecisionLog.ticker == ticker.upper().strip())
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows)

    async def evaluate_outcome(self, decision_id: int) -> DecisionLog:
        """Fill realized outcome fields for one decision."""
        row = await self._get_decision_or_raise(decision_id)
        outcome_price = await self._safe_get_current_price(row.ticker)
        if outcome_price is None or row.price_at_decision in (None, 0):
            raise ValueError("Cannot evaluate outcome without current price and price_at_decision")

        pnl_pct = (outcome_price - row.price_at_decision) / row.price_at_decision * 100
        verdict = self._infer_outcome_verdict(row.decision_type, pnl_pct)

        row.outcome_price = outcome_price
        row.outcome_pnl_pct = pnl_pct
        row.outcome_evaluated_at = datetime.now(UTC)
        row.outcome_verdict = verdict
        await self._session.commit()
        await self._session.refresh(row)
        logger.info(
            "decision.outcome_evaluated",
            decision_id=row.id,
            ticker=row.ticker,
            pnl_pct=pnl_pct,
            verdict=verdict,
        )
        return row

    async def analyze_decision(self, decision_id: int) -> DecisionReplayEnvelope:
        """Run AI replay analysis after outcome is evaluated."""
        row = await self._get_decision_or_raise(decision_id)
        if row.outcome_evaluated_at is None:
            raise ValueError("Decision outcome not evaluated yet")

        if self._replay_agent is None:
            return DecisionReplayEnvelope(
                decision_id=row.id,
                ticker=row.ticker,
                outcome_verdict=str(row.outcome_verdict) if row.outcome_verdict else None,
                replay=None,
            )

        ctx = ReplayContext(
            decision_id=row.id,
            thesis_id=row.thesis_id,
            ticker=row.ticker,
            decision_type=row.decision_type,
            decision_at=row.decision_at.isoformat(),
            rationale=row.rationale,
            price_at_decision=row.price_at_decision,
            thesis_score_at_decision=row.thesis_score_at_decision,
            thesis_health_score_at_decision=row.thesis_health_score_at_decision,
            active_signal=row.active_signal,
            brief_summary=row.brief_summary,
            outcome_price=row.outcome_price,
            outcome_pnl_pct=row.outcome_pnl_pct,
            outcome_horizon_days=row.review_horizon_days,
            outcome_verdict_hint=row.outcome_verdict,
        )
        replay = await self._replay_agent.analyze(  # type: ignore[func-returns-value]
            ctx,
            session=self._session,
            user_id=str(row.user_id),
            trigger="decision_replay",
        )
        return DecisionReplayEnvelope(
            decision_id=row.id,
            ticker=row.ticker,
            outcome_verdict=str(row.outcome_verdict) if row.outcome_verdict else None,
            replay=replay,
        )

    async def persist_lesson(
        self,
        decision_id: int,
        *,
        key_lesson: str | None,
        pattern_detected: str | None,
    ) -> DecisionLog:
        """Write AI-generated lesson and pattern back to DecisionLog."""
        row = await self._get_decision_or_raise(decision_id)
        updated = False

        if key_lesson is not None:
            row.key_lesson = key_lesson
            updated = True

        if pattern_detected is not None:
            row.pattern_detected = pattern_detected
            updated = True

        if updated:
            await self._session.commit()
            await self._session.refresh(row)
            logger.info(
                "decision.lesson_persisted",
                decision_id=row.id,
                ticker=row.ticker,
                has_lesson=key_lesson is not None,
                has_pattern=pattern_detected is not None,
            )
        else:
            logger.debug(
                "decision.lesson_persist_skipped",
                decision_id=decision_id,
                reason="both key_lesson and pattern_detected are None",
            )

        return row

    async def replay_decision(
        self,
        decision_id: int,
        user_id: str,
    ) -> DecisionReplayEnvelope:
        """Load, ownership-check, evaluate if needed, analyze, persist lesson.

        After persisting a key_lesson, publishes ThesisReviewRequestedEvent so
        the thesis review listener can run an AI review on the linked thesis.
        Fire-and-forget: thesis review failure never blocks the replay response.
        """
        row = await self._get_decision_or_raise(decision_id)
        if str(row.user_id) != str(user_id):
            raise DecisionNotFoundError(
                f"Decision #{decision_id} not found or does not belong to this user."
            )

        if row.outcome_evaluated_at is None:
            await self.evaluate_outcome(decision_id)

        envelope = await self.analyze_decision(decision_id)

        if envelope.replay is not None:
            await self.persist_lesson(
                decision_id,
                key_lesson=getattr(envelope.replay, "key_lesson", None),
                pattern_detected=getattr(envelope.replay, "pattern_detected", None),
            )
            # Close the lesson → thesis review loop: if a key_lesson was produced,
            # request an AI review of the linked thesis so it can incorporate the
            # new evidence. Guard: only ACTIVE theses qualify.
            if getattr(envelope.replay, "key_lesson", None) and row.thesis_id:
                await self._maybe_request_thesis_review(row.thesis_id, row.ticker)

        return envelope

    async def _maybe_request_thesis_review(
        self,
        thesis_id: int,
        ticker: str,
    ) -> None:
        """Publish ThesisReviewRequestedEvent after a lesson is persisted.

        Guard: thesis must be ACTIVE — non-active theses are silently skipped.
        Fire-and-forget: any exception is logged as WARNING, never re-raised.

        Owner: thesis segment (decision → thesis review loop).
        """
        try:
            thesis = await self._repo.get_by_id(thesis_id)
            if thesis is None or thesis.status != "active":
                logger.info(
                    "decision_service.skip_thesis_review_request",
                    thesis_id=thesis_id,
                    reason="not_active_or_not_found",
                    status=str(thesis.status) if thesis else "not_found",
                )
                return

            from src.platform.event_bus import get_event_bus
            from src.platform.events import ThesisReviewRequestedEvent

            await get_event_bus().publish(ThesisReviewRequestedEvent(
                thesis_id=str(thesis_id),
                symbol=ticker,
                reason="lesson_from_replay",
            ))
            logger.info(
                "decision_service.thesis_review_requested_after_lesson",
                thesis_id=thesis_id,
                ticker=ticker,
            )
        except Exception as exc:
            logger.warning(
                "decision_service.thesis_review_request_failed",
                thesis_id=thesis_id,
                ticker=ticker,
                error=str(exc),
            )

    async def _get_decision_or_raise(self, decision_id: int) -> DecisionLog:
        stmt = select(DecisionLog).where(DecisionLog.id == decision_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise ValueError(f"DecisionLog {decision_id} not found")
        return row

    async def _safe_get_current_price(self, ticker: str) -> float | None:
        if self._quote_service is None:
            return None
        try:
            quote = await self._quote_service.get_quote(ticker)  # type: ignore[attr-defined]
            return float(quote.price)
        except Exception as exc:
            logger.warning("decision.price_lookup_failed", ticker=ticker, error=str(exc))
            return None

    def _infer_current_thesis_score(self, thesis: Thesis) -> float | None:
        if not thesis.snapshots:
            return None
        latest = max(thesis.snapshots, key=lambda s: s.snapshotted_at)
        return float(latest.score) if latest.score is not None else None

    def _infer_current_health_score(self, thesis: Thesis) -> int | None:
        if not thesis.snapshots:
            return None
        latest = max(thesis.snapshots, key=lambda s: s.snapshotted_at)
        return int(latest.score) if latest.score is not None else None

    def _infer_outcome_verdict(self, decision_type: str, pnl_pct: float) -> str:
        if decision_type in {"BUY", "ADD"}:
            if pnl_pct >= 5:
                return "CORRECT"
            if pnl_pct <= -5:
                return "INCORRECT"
            return "MIXED"
        if decision_type in {"SELL", "REDUCE"}:
            if pnl_pct <= -5:
                return "CORRECT"
            if pnl_pct >= 5:
                return "INCORRECT"
            return "MIXED"
        return "MIXED"
