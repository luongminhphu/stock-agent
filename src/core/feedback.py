"""
FeedbackStore — persistence layer for engine verdict feedback.
Owner: core segment.

ORM model: EngineFeedback
Service:   FeedbackStore

Purpose:
  Record user-supplied outcome for each EngineVerdict so Wave 4
  (evolution.py) can analyse patterns and propose signal reweights.

Convention: follows repo ORM pattern — Base from src.platform.db,
  AsyncSessionLocal for session management, no synchronous code.

Table: engine_feedback
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import DateTime, Float, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.db import AsyncSessionLocal, Base
from src.platform.logging import get_logger

logger = get_logger(__name__)


class EngineFeedback(Base):
    """Stores user feedback on an EngineVerdict cycle.

    One row per feedback submission. Multiple submissions for the same
    verdict_event_id are allowed (user may revise).

    delta_score is computed by the submitter and stored as-is.
    Wave 4 evolution.py reads this column to reweight signal sources.
    """
    __tablename__ = "engine_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    verdict_event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # correct | incorrect | partial | not_acted
    trigger_source: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    delta_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


# outcome → delta_score mapping
# Positive = reinforces the signal sources that produced this verdict.
# Negative = penalises them. Used by Wave 4 to reweight.
_OUTCOME_DELTA: dict[str, float] = {
    "correct":    +0.15,
    "partial":    +0.05,
    "not_acted":   0.00,
    "incorrect":  -0.20,
}


class FeedbackStore:
    """Async service for persisting and querying EngineFeedback rows.

    Usage::

        await FeedbackStore.record(
            verdict_event_id="abc-123",
            user_id="discord:123456",
            verdict="BUY_SIGNAL",
            outcome="correct",
            trigger_source="scheduler:pre_market",
            user_note="VCB broke out as expected",
        )
    """

    @staticmethod
    async def record(
        verdict_event_id: str,
        user_id: str,
        verdict: str,
        outcome: Literal["correct", "incorrect", "partial", "not_acted"],
        trigger_source: str = "",
        user_note: str | None = None,
    ) -> EngineFeedback:
        """Persist a feedback entry. Returns the saved ORM row."""
        delta = _OUTCOME_DELTA.get(outcome, 0.0)
        entry = EngineFeedback(
            verdict_event_id=verdict_event_id,
            user_id=user_id,
            verdict=verdict,
            outcome=outcome,
            trigger_source=trigger_source,
            user_note=user_note,
            delta_score=delta,
        )
        async with AsyncSessionLocal() as session:
            session.add(entry)
            await session.commit()
            await session.refresh(entry)

        logger.info(
            "feedback_store.recorded",
            verdict_event_id=verdict_event_id,
            outcome=outcome,
            delta_score=delta,
        )
        return entry

    @staticmethod
    async def get_recent(
        days: int = 30,
        user_id: str | None = None,
    ) -> list[EngineFeedback]:
        """Return feedback entries from the last N days.

        Args:
            days:    Look-back window (default 30).
            user_id: Optional filter. None returns all users.

        Returns:
            List of EngineFeedback ordered by submitted_at DESC.
        """
        cutoff = datetime.now(UTC) - timedelta(days=days)
        async with AsyncSessionLocal() as session:
            stmt = (
                select(EngineFeedback)
                .where(EngineFeedback.submitted_at >= cutoff)
                .order_by(EngineFeedback.submitted_at.desc())
            )
            if user_id:
                stmt = stmt.where(EngineFeedback.user_id == user_id)
            result = await session.execute(stmt)
            return list(result.scalars().all())
