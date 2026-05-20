"""Repository for ai.memory models.

Owner: ai segment.
Callers: memory_service.py, consolidator.py, episodic_store.py only.
No business logic — pure persistence.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.memory.models import AIInteractionLog, MemorySnapshot


class InteractionLogRepository:
    """Persistence for AIInteractionLog (Layer 2 — Episodic Memory)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, log: AIInteractionLog) -> AIInteractionLog:
        self._session.add(log)
        await self._session.flush()
        return log

    async def get_recent(
        self,
        user_id: str,
        limit: int = 20,
        agent_type: str | None = None,
    ) -> list[AIInteractionLog]:
        """Return the most recent N logs for a user, newest first."""
        stmt = (
            select(AIInteractionLog)
            .where(AIInteractionLog.user_id == user_id)
            .order_by(desc(AIInteractionLog.created_at))
            .limit(limit)
        )
        if agent_type:
            stmt = stmt.where(AIInteractionLog.agent_type == agent_type)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_since(
        self,
        user_id: str,
        since: datetime,
        limit: int = 200,
    ) -> list[AIInteractionLog]:
        """Return all logs after a given datetime (for consolidation)."""
        stmt = (
            select(AIInteractionLog)
            .where(
                AIInteractionLog.user_id == user_id,
                AIInteractionLog.created_at >= since,
            )
            .order_by(AIInteractionLog.created_at)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Signal & outcome (called by EpisodicStore only)
    # ------------------------------------------------------------------

    async def get_by_id(self, log_id: int) -> AIInteractionLog | None:
        """Fetch a single log by PK."""
        stmt = select(AIInteractionLog).where(AIInteractionLog.id == log_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def set_user_signal(self, log_id: int, signal: str) -> None:
        """Record user reaction (bought/sold/ignored/flagged/watched)."""
        stmt = (
            update(AIInteractionLog)
            .where(AIInteractionLog.id == log_id)
            .values(user_signal=signal)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def set_outcome(self, log_id: int, outcome: dict) -> None:
        """Persist price outcome dict as JSON. Caller must check idempotency."""
        stmt = (
            update(AIInteractionLog)
            .where(AIInteractionLog.id == log_id)
            .values(outcome_json=json.dumps(outcome, ensure_ascii=False))
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def get_pending_outcome(
        self,
        older_than_days: int = 7,
        limit: int = 50,
    ) -> list[AIInteractionLog]:
        """Logs with user_signal set but outcome_json not yet filled.

        Only returns logs older than `older_than_days` so price has settled.
        Caller: outcome_filler scheduler job.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        stmt = (
            select(AIInteractionLog)
            .where(
                AIInteractionLog.user_signal.isnot(None),
                AIInteractionLog.outcome_json.is_(None),
                AIInteractionLog.created_at <= cutoff,
            )
            .order_by(AIInteractionLog.created_at)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_symbols(
        self,
        symbols: list[str],
        limit: int = 20,
    ) -> list[AIInteractionLog]:
        """Recent episodes that mention any of the given ticker symbols.

        Uses LIKE matching on tickers_json text column — acceptable for
        watchlists < 30 tickers. Caller: EpisodicStore / MemoryContextBuilder.
        """
        if not symbols:
            return []
        from sqlalchemy import or_
        conditions = [
            AIInteractionLog.tickers_json.contains(sym) for sym in symbols
        ]
        stmt = (
            select(AIInteractionLog)
            .where(or_(*conditions))
            .order_by(desc(AIInteractionLog.created_at))
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class MemorySnapshotRepository:
    """Persistence for MemorySnapshot (Layer 3 — Semantic Memory)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, snapshot: MemorySnapshot) -> MemorySnapshot:
        self._session.add(snapshot)
        await self._session.flush()
        return snapshot

    async def get_latest(self, user_id: str) -> MemorySnapshot | None:
        """Return the most recent snapshot for a user."""
        stmt = (
            select(MemorySnapshot)
            .where(MemorySnapshot.user_id == user_id)
            .order_by(desc(MemorySnapshot.created_at))
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(
        self, user_id: str, limit: int = 10
    ) -> list[MemorySnapshot]:
        stmt = (
            select(MemorySnapshot)
            .where(MemorySnapshot.user_id == user_id)
            .order_by(desc(MemorySnapshot.created_at))
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
