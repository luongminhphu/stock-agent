"""Briefing ORM models.

Owner: briefing segment.

Tables:
  brief_snapshots — persisted output of each morning/EOD brief generation.

Design rules:
- BriefSnapshot is write-side truth for brief history.
- readmodel.dashboard_service reads this table directly (same DB, no import
  of domain logic — only ORM model). That is an acceptable readmodel pattern.
- No domain logic here. No AI calls. No formatting.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.db import Base


class BriefSnapshot(Base):
    """Persisted output of a single brief generation run.

    Columns:
        id            — surrogate PK
        user_id       — owner (Discord user id or internal user id)
        phase         — "morning" | "eod"
        content       — full formatted brief text (Markdown)
        tickers       — comma-separated watchlist tickers at generation time
        created_at    — UTC timestamp of generation
    """

    __tablename__ = "brief_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)  # "morning" | "eod"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tickers: Mapped[str | None] = mapped_column(String(512), nullable=True)  # CSV snapshot
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        # Fast lookup: latest brief for a user+phase
        Index("ix_brief_snapshots_user_phase_created", "user_id", "phase", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<BriefSnapshot id={self.id} user={self.user_id!r} "
            f"phase={self.phase!r} created_at={self.created_at}>"
        )
