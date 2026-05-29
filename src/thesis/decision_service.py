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

# Threshold for verdict classification (percentage points).
# price movement >= +_VERDICT_THRESHOLD_PCT → CORRECT for bullish decisions.
# price movement <= -_VERDICT_THRESHOLD_PCT → INCORRECT for bullish decisions.
_VERDICT_THRESHOLD_PCT = 5.0


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
        execution_price: float | None = None,
        quantity: int | None = None,
        brief_summary: str | None = None,
        active_signal: str | None = None,
        review_horizon_days: int = _DEFAULT_REVIEW_HORIZON_DAYS,
    ) -> DecisionLog:
        """Persist one immutable decision with frozen context at decision time.

        execution_price: actual fill price supplied by the user ("Giá thực hiện").
            If provided, stored as price_at_decision directly.
            If omitted, live quote from quote_service is used as fallback.
        quantity: number of shares traded — stored as context, not used in PnL calc.
        """
        decision_type = decision_type.upper().strip()
        if decision_type not in _VALID_DECISION_TYPES:
            raise ValueError(f"Unsupported decision_type={decision_type}")

        thesis = await self._repo.get_by_id(thesis_id)
        if thesis is None:
            raise ValueError(f"Thesis #{thesis_id} not found")
        if str(thesis.user_id) != str(user_id):
            raise PermissionError(f"Thesis #{thesis_id} does not belong to this user")

        # execution_price takes priority; fall back to live quote
        if execution_price is not None:
            current_price = float(execution_price)
        else:
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
            quantity=quantity,
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
            price_at_decision=current_price,
            quantity=quantity,
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
        """List decision logs for a user, newest first."""
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

    async def list_pending_outcome_evaluations(self) -> list[DecisionLog]:
        """Return DecisionLog rows that have reached their review horizon
        but have not yet been outcome-evaluated.

        A decision is considered pending when:
          - outcome_evaluated_at IS NULL (never evaluated), AND
          - decision_at + review_horizon_days <= now (horizon has elapsed).

        Ordered oldest-first so the scheduler processes in chronological order.
        No user_id filter — the scheduler runs across all users.
        """
        now = datetime.now(UTC)
        # Fetch all unevaluated rows first, then filter in Python using the
        # per-row review_horizon_days value. This avoids DB-specific interval
        # arithmetic and keeps the query portable across SQLite (tests) and
        # PostgreSQL (production).
        stmt = (
            select(DecisionLog)
            .where(DecisionLog.outcome_evaluated_at.is_(None))
            .order_by(DecisionLog.decision_at.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            row for row in rows
            if row.decision_at is not None
            and row.decision_at + timedelta(days=row.review_horizon_days) <= now
        ]

    async def evaluate_outcome(self, decision_id: int) -> DecisionLog:
        """Fill realized outcome fields for one decision.

        outcome_pnl_pct represents price movement % from price_at_decision
        to outcome_price over the review horizon. Verdict semantics differ
        per decision_type — see _infer_outcome_verdict().

        Raises ValueError if:
          - Current price cannot be fetched (quote_service unavailable).
          - price_at_decision is None (quote_service was None at log time
            and execution_price was not provided by the user).
          - price_at_decision is zero (corrupt row).
        """
        row = await self._get_decision_or_raise(decision_id)
        outcome_price = await self._safe_get_current_price(row.ticker)

        if outcome_price is None:
            raise ValueError(
                f"Cannot evaluate outcome for decision #{decision_id} "
                f"(ticker={row.ticker}, type={row.decision_type}): "
                "failed to fetch current price from quote_service."
            )
        if row.price_at_decision is None:
            raise ValueError(
                f"Cannot evaluate outcome for decision #{decision_id} "
                f"(ticker={row.ticker}, type={row.decision_type}): "
                "price_at_decision is NULL — execution_price was not provided "
                "and quote_service was unavailable at log time."
            )
        if row.price_at_decision == 0:
            raise ValueError(
                f"Cannot evaluate outcome for decision #{decision_id} "
                f"(ticker={row.ticker}, type={row.decision_type}): "
                "price_at_decision=0 is invalid (corrupt row)."
            )

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
            decision_type=row.decision_type,
            price_at_decision=row.price_at_decision,
            outcome_price=outcome_price,
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
        """Load, ownership-check, evaluate if needed, analyze, persist lesson."""
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
            if getattr(envelope.replay, "key_lesson", None) and row.thesis_id:
                await self._maybe_request_thesis_review(row.thesis_id, row.ticker)

        return envelope

    async def _maybe_request_thesis_review(
        self,
        thesis_id: int,
        ticker: str,
    ) -> None:
        """Publish ThesisReviewRequestedEvent after a lesson is persisted."""
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
        """Freeze latest thesis score (composite AI score) at decision time.

        Reads ThesisSnapshot.score — the overall thesis quality score
        written by review_service after each ThesisReview.
        """
        if not thesis.snapshots:
            return None
        latest = max(thesis.snapshots, key=lambda s: s.snapshotted_at)
        return float(latest.score) if latest.score is not None else None

    def _infer_current_health_score(self, thesis: Thesis) -> int | None:
        """Freeze latest thesis health (conviction) at decision time as int 0–100.

        Reads ThesisSnapshot.conviction_score (float 0.0–1.0) — the AI’s
        confidence that the thesis is still valid, written by review_service
        after each ThesisReview. Scaled x100 to int to match the Integer
        column contract of thesis_health_score_at_decision.

        Fallback chain:
          1. Latest snapshot with conviction_score not None → int(v * 100), clamped 0–100.
          2. No snapshots or conviction_score is None → None (stored as NULL).

        This is intentionally different from _infer_current_thesis_score(),
        which reads .score (composite quality). Together they give ReplayAgent
        two independent signals: raw thesis quality vs AI conviction.
        """
        if not thesis.snapshots:
            return None
        latest = max(thesis.snapshots, key=lambda s: s.snapshotted_at)
        if latest.conviction_score is None:
            return None
        return max(0, min(100, int(round(latest.conviction_score * 100))))

    def _infer_outcome_verdict(self, decision_type: str, pnl_pct: float) -> str:
        """Map price movement % to an outcome verdict given the decision type.

        Semantics per type:
          BUY / ADD / HOLD: bullish bias — price rising confirms the call.
            >= +_VERDICT_THRESHOLD_PCT  → CORRECT
            <= -_VERDICT_THRESHOLD_PCT  → INCORRECT
            in between              → MIXED

          SELL / REDUCE: bearish/exit bias — price falling confirms the call.
            <= -_VERDICT_THRESHOLD_PCT  → CORRECT
            >= +_VERDICT_THRESHOLD_PCT  → INCORRECT
            in between              → MIXED

        HOLD is treated identically to BUY: holding a position implies the
        investor expected the price to hold or rise. A significant decline
        means the hold decision was wrong.
        """
        t = _VERDICT_THRESHOLD_PCT

        if decision_type in {"BUY", "ADD", "HOLD"}:
            if pnl_pct >= t:
                return "CORRECT"
            if pnl_pct <= -t:
                return "INCORRECT"
            return "MIXED"

        if decision_type in {"SELL", "REDUCE"}:
            if pnl_pct <= -t:
                return "CORRECT"
            if pnl_pct >= t:
                return "INCORRECT"
            return "MIXED"

        # Safety fallback for any future decision_type extension.
        return "MIXED"
