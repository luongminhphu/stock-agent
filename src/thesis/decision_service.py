"""Decision service for Decision Replay & Learning Loop.

Owner: thesis segment.

Responsibilities:
- Persist immutable decision logs (BUY / SELL / HOLD / ADD / REDUCE).
- Freeze relevant thesis + market context at decision time.
- Evaluate realized outcome after a review horizon (30/90 days, configurable).
- Call ReplayAgent to produce personalized lessons.
- Persist AI-generated key_lesson and pattern_detected back to DecisionLog.

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

        thesis = await self._repo.get_or_raise(thesis_id, user_id)
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

    async def list_pending_outcome_evaluations(self) -> list[DecisionLog]:
        """Decisions that reached horizon but do not have realized outcome yet."""
        stmt = select(DecisionLog).where(DecisionLog.outcome_evaluated_at.is_(None))
        rows = (await self._session.execute(stmt)).scalars().all()
        now = datetime.now(UTC)
        return [
            r for r in rows
            if r.decision_at + timedelta(days=r.review_horizon_days) <= now
        ]

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
                outcome_verdict=row.outcome_verdict,
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
        replay = await self._replay_agent.analyze(ctx)  # type: ignore[func-returns-value]
        return DecisionReplayEnvelope(
            decision_id=row.id,
            ticker=row.ticker,
            outcome_verdict=row.outcome_verdict,
            replay=replay,
        )

    async def persist_lesson(
        self,
        decision_id: int,
        *,
        key_lesson: str | None,
        pattern_detected: str | None,
    ) -> DecisionLog:
        """Write AI-generated lesson and pattern back to DecisionLog.

        Called by DecisionReplayScheduler after analyze_decision() succeeds.
        This closes the learning loop: ReplayAgent insight → stored in DB →
        surfaced by LessonService → injected into future briefing / pretrade prompts.

        Only updates fields that have a non-None value — never overwrites
        an existing lesson with None.

        Args:
            decision_id:       PK of the DecisionLog to update.
            key_lesson:        The primary takeaway from ReplayAgent (1-2 sentences).
            pattern_detected:  Short label for the pattern, e.g. 'breakout_chasing'.

        Returns:
            Updated DecisionLog row (refreshed).

        Raises:
            ValueError: If decision_id not found.
        """
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
        latest = max(thesis.snapshots, key=lambda s: s.created_at)
        return float(latest.score)

    def _infer_current_health_score(self, thesis: Thesis) -> int | None:
        # No dedicated persisted health table yet. Reuse latest snapshot confidence/score as fallback.
        if not thesis.snapshots:
            return None
        latest = max(thesis.snapshots, key=lambda s: s.created_at)
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

        # HOLD depends on stability; neutral move counts mixed by default
        if abs(pnl_pct) < 5:
            return "CORRECT"
        return "MIXED"
