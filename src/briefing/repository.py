"""BriefSnapshot repository — persistence contract for the briefing segment.

Owner: briefing segment.

This is the ONLY place that writes to the brief_snapshots table.
readmodel.dashboard_service reads it directly via SQLAlchemy SELECT
(same DB) without going through this repository — that is intentional
and acceptable for a readmodel pattern.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.briefing.models import BriefSnapshot
from src.platform.logging import get_logger

logger = get_logger(__name__)


class BriefSnapshotRepository:
    """Async repository for BriefSnapshot.

    All methods accept an AsyncSession injected from the caller
    (BriefingService). The session lifecycle (commit / rollback)
    is managed by the caller, not here.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, snapshot: BriefSnapshot) -> BriefSnapshot:
        """Persist a new BriefSnapshot. Returns the saved instance (with id).

        The caller is responsible for committing the session.
        """
        self._session.add(snapshot)
        await self._session.flush()  # populate id without committing
        logger.debug(
            "brief_snapshot.saved",
            snapshot_id=snapshot.id,
            user_id=snapshot.user_id,
            phase=snapshot.phase,
        )
        return snapshot

    async def get_latest(
        self,
        user_id: str,
        phase: str,
    ) -> BriefSnapshot | None:
        """Return the most recent BriefSnapshot for user+phase, or None."""
        result = await self._session.execute(
            select(BriefSnapshot)
            .where(
                BriefSnapshot.user_id == user_id,
                BriefSnapshot.phase == phase,
            )
            .order_by(BriefSnapshot.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_recent(
        self,
        user_id: str,
        phase: str | None = None,
        limit: int = 10,
    ) -> list[BriefSnapshot]:
        """Return up to `limit` most recent snapshots for a user.

        Optionally filter by phase ("morning" | "eod").
        """
        stmt = (
            select(BriefSnapshot)
            .where(BriefSnapshot.user_id == user_id)
            .order_by(BriefSnapshot.created_at.desc())
            .limit(min(limit, 100))
        )
        if phase is not None:
            stmt = stmt.where(BriefSnapshot.phase == phase)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
