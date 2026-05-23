"""FeedbackStore — persists user verdict outcomes for self-improvement loop.

Owner: core segment.
Written by: api (POST /core/feedback) and bot (!feedback command).
Read by: evolution.py for pattern analysis.

Wave 1: in-memory store (list). No DB dependency.
Wave 3: replace with async DB writes to core_feedback table.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

from src.core.schemas import FeedbackEntry


class FeedbackStore:
    """Simple in-memory feedback store for Wave 1.

    Replace _store with DB session writes in Wave 3.
    """

    _store: ClassVar[list[dict]] = []

    @classmethod
    async def record(
        cls,
        verdict_id: str,
        outcome: str,
        user_note: str | None = None,
        delta_score: float = 0.0,
    ) -> FeedbackEntry:
        entry = FeedbackEntry(
            verdict_id=verdict_id,
            outcome=outcome,  # type: ignore[arg-type]
            user_note=user_note,
            delta_score=delta_score,
        )
        cls._store.append({
            **entry.model_dump(),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })
        return entry

    @classmethod
    async def get_recent(cls, limit: int = 100) -> list[dict]:
        """Return most recent N feedback entries."""
        return cls._store[-limit:]

    @classmethod
    async def get_by_verdict(cls, verdict_id: str) -> list[dict]:
        return [e for e in cls._store if e["verdict_id"] == verdict_id]

    @classmethod
    def reset(cls) -> None:
        """Test helper — clear all stored feedback."""
        cls._store.clear()
