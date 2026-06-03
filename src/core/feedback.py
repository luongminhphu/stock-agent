"""FeedbackStore — persists user verdict outcomes for self-improvement loop.

Owner: core segment.
Written by: EngineFeedbackListener (via EngineFeedbackSubmittedEvent).
Read by:    evolution.py for pattern analysis.

Storage strategy:
    Primary:  core_feedback table via SQLAlchemy async session (get_session).
    Fallback: in-memory _store list — used when DB is unavailable so that
              feedback events are never silently dropped during a DB outage.
              The fallback store is NOT persisted across restarts; it exists
              only to prevent data loss during transient failures.

Public API (called by feedback_listener.py)::

    entry = await FeedbackStore.record(
        verdict_event_id=event.verdict_event_id,
        user_id=event.user_id,
        verdict=event.verdict,
        outcome=event.outcome,
        trigger_source=event.trigger_source,
        user_note=event.user_note or None,
    )

Public API (called by evolution.py)::

    rows = await FeedbackStore.get_recent(limit=200)
    rows = await FeedbackStore.get_by_verdict_event(verdict_event_id)
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import ClassVar

from src.core.models import CoreFeedback
from src.core.schemas import FeedbackEntry, FeedbackOutcome
from src.platform.db import get_session

logger = logging.getLogger(__name__)


class FeedbackStore:
    """Write-through feedback store: DB primary, in-memory fallback.

    All methods are async classmethods — no instance state required.
    """

    # Fallback buffer — populated only when DB write fails
    _store: ClassVar[list[dict]] = []

    # ---------------------------------------------------------------------------
    # Write path
    # ---------------------------------------------------------------------------

    @classmethod
    async def record(
        cls,
        verdict_event_id: str,
        user_id: str = "",
        verdict: str = "",
        outcome: FeedbackOutcome = "not_acted",
        trigger_source: str = "",
        user_note: str | None = None,
        delta_score: float = 0.0,
    ) -> FeedbackEntry:
        """Persist one feedback record to DB, falling back to in-memory on error.

        Args:
            verdict_event_id: UUID of the IntelligenceEngineCompletedEvent.
            user_id:          Investor identifier.
            verdict:          Engine verdict string (e.g. "BUY_SIGNAL").
            outcome:          User's assessment of the verdict quality.
            trigger_source:   Origin of the feedback ("bot", "api", …).
            user_note:        Optional free-text comment.
            delta_score:      Reserved for evolution scoring (default 0.0).

        Returns:
            FeedbackEntry Pydantic model (caller can log/inspect).
        """
        entry = FeedbackEntry(
            verdict_event_id=verdict_event_id,
            user_id=user_id,
            verdict=verdict,
            outcome=outcome,
            trigger_source=trigger_source,
            user_note=user_note,
            delta_score=delta_score,
        )

        db_ok = await cls._write_to_db(entry)
        if not db_ok:
            # DB unavailable — keep in memory so evolution.py can still read
            cls._store.append({
                **entry.model_dump(),
                "recorded_at": datetime.now(UTC).isoformat(),
            })
            logger.warning(
                "feedback_store.db_unavailable_fallback",
                extra={"verdict_event_id": verdict_event_id},
            )

        return entry

    @classmethod
    async def _write_to_db(cls, entry: FeedbackEntry) -> bool:
        """Write entry to core_feedback table. Returns True on success."""
        try:
            async with get_session() as session:
                row = CoreFeedback(
                    verdict_event_id=entry.verdict_event_id,
                    user_id=entry.user_id,
                    verdict=entry.verdict,
                    outcome=entry.outcome,
                    trigger_source=entry.trigger_source,
                    user_note=entry.user_note,
                    delta_score=entry.delta_score,
                )
                session.add(row)
                # commit handled by get_session() context manager
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "feedback_store.db_write_failed",
                extra={"error": str(exc)},
            )
            return False

    # ---------------------------------------------------------------------------
    # Read path — evolution.py entry points
    # ---------------------------------------------------------------------------

    @classmethod
    async def get_recent(cls, limit: int = 200) -> list[dict]:
        """Return most recent N feedback records.

        Queries DB first. If DB is unavailable, falls back to in-memory store.
        Each record is a plain dict matching FeedbackEntry.model_dump() shape
        plus a 'recorded_at' ISO string.
        """
        try:
            from sqlalchemy import select
            async with get_session() as session:
                stmt = (
                    select(CoreFeedback)
                    .order_by(CoreFeedback.recorded_at.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                rows = result.scalars().all()
                return [
                    {
                        "verdict_event_id": r.verdict_event_id,
                        "user_id": r.user_id,
                        "verdict": r.verdict,
                        "outcome": r.outcome,
                        "trigger_source": r.trigger_source,
                        "user_note": r.user_note,
                        "delta_score": r.delta_score,
                        "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
                    }
                    for r in rows
                ]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "feedback_store.get_recent_db_failed",
                extra={"error": str(exc)},
            )
            return cls._store[-limit:]

    @classmethod
    async def get_by_verdict_event(
        cls,
        verdict_event_id: str,
    ) -> list[dict]:
        """Return all feedback records for a given verdict_event_id.

        Replaces the old get_by_verdict(verdict_id) — field renamed to
        verdict_event_id throughout to match event schema.
        """
        try:
            from sqlalchemy import select
            async with get_session() as session:
                stmt = select(CoreFeedback).where(
                    CoreFeedback.verdict_event_id == verdict_event_id
                )
                result = await session.execute(stmt)
                rows = result.scalars().all()
                return [
                    {
                        "verdict_event_id": r.verdict_event_id,
                        "user_id": r.user_id,
                        "verdict": r.verdict,
                        "outcome": r.outcome,
                        "trigger_source": r.trigger_source,
                        "user_note": r.user_note,
                        "delta_score": r.delta_score,
                        "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
                    }
                    for r in rows
                ]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "feedback_store.get_by_verdict_event_db_failed",
                extra={"error": str(exc)},
            )
            return [
                e for e in cls._store
                if e.get("verdict_event_id") == verdict_event_id
            ]

    # ---------------------------------------------------------------------------
    # Test helpers
    # ---------------------------------------------------------------------------

    @classmethod
    def reset(cls) -> None:
        """Clear in-memory fallback store. Use in tests only."""
        cls._store.clear()
