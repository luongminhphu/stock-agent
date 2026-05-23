"""FeedbackStore — persist và query verdict outcomes.

Owner: core segment.

Wave 1: in-memory stub — echo only, no DB write.
Wave 3: persist to Postgres via SQLAlchemy (table: core_feedback).
         Used by evolution.py to detect failure patterns and
         by SignalFilter to reweight signal scores.

Contract: FeedbackStore.record() is fire-and-forget safe.
Callers must not rely on persistence in Wave 1.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.core.schemas import FeedbackEntry


class FeedbackStore:
    """Persist and retrieve feedback entries.

    Wave 1: all methods are stubs that return immediately.
    Wave 3: inject AsyncSession and write to core_feedback table.
    """

    # Wave 1: in-memory log for debugging
    _log: list[dict] = []

    @classmethod
    async def record(cls, entry: FeedbackEntry) -> None:
        """Persist a feedback entry.

        Wave 1: append to in-memory log.
        Wave 3: INSERT INTO core_feedback (verdict_id, outcome, user_note, created_at).
        """
        cls._log.append({
            "verdict_id": entry.verdict_id,
            "outcome": entry.outcome,
            "user_note": entry.user_note,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })

    @classmethod
    async def get_recent(cls, days: int = 30) -> list[dict]:
        """Return recent feedback entries.

        Wave 1: return in-memory log.
        Wave 3: SELECT FROM core_feedback WHERE created_at >= now() - interval.
        """
        # Wave 1: return all (no date filtering)
        return list(cls._log)
