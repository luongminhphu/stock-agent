"""Repository for ai.memory models.

Owner: ai segment.
Callers: memory_service.py, consolidator.py only.
No business logic — pure persistence.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, select
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
