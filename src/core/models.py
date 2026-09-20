"""Core segment — SQLAlchemy ORM models.

Owner: core segment.

Tables:
    core_feedback           — persisted FeedbackEntry records written by FeedbackStore.
    intelligence_snapshots  — IntelligenceReport mới nhất per user (Wave E1b, từ readmodel)
    global_risk_snapshots   — GlobalRisk verdict per user (Wave E1b, từ readmodel)

Wave E1b: hai bảng snapshot là output của core engine → owner core. Store/cache
(readmodel.intelligence_snapshot, readmodel.global_risk_store) là projection và
chỉ import ORM từ đây; tên bảng giữ nguyên → không migration.

Migration:
    This table is created by Alembic (or create_all in dev).
    To add the table in an existing DB::

        from src.platform.db import engine
        from src.core.models import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    Production: generate a new Alembic revision targeting this model.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.db import Base


class CoreFeedback(Base):
    """Persistent record of one investor feedback event.

    Maps to the core_feedback table. Each row corresponds to one
    EngineFeedbackSubmittedEvent processed by FeedbackStore.record().

    Columns mirror FeedbackEntry fields 1:1 so evolution.py can read
    either the Pydantic model (in-memory) or the ORM row (DB) without
    data transformation.
    """

    __tablename__ = "core_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Core identity — links back to the engine run that produced the verdict
    verdict_event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # What the engine said and what the user thought about it
    verdict: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="not_acted")
    trigger_source: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    # Optional free-text note from the investor
    user_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Reserved for self-improvement scoring (evolution.py)
    delta_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Auto-set on insert — never updated
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"CoreFeedback(id={self.id}, user={self.user_id!r}, "
            f"verdict={self.verdict!r}, outcome={self.outcome!r})"
        )


class IntelligenceSnapshot(Base):
    """Persisted IntelligenceReport per user — IntelligenceSnapshotStore warm layer.

    Ensures GET /readmodel/dashboard/intelligence always has something to return
    after a restart, avoiding the 204 cold-start problem.

    PK: user_id (one current snapshot per user, upserted after every engine cycle).
    """

    __tablename__ = "intelligence_snapshots"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="JSON: IntelligenceReport.model_dump()",
    )
    trigger_source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unknown",
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class GlobalRiskSnapshot(Base):
    """Persisted GlobalRiskStore verdict per user.

    Ensures BriefingService/ScanService/ThesisReviewService still read
    flagged tickers from the last engine cycle after a restart.

    TTL: same 4h window as GlobalRiskStore._entries.is_fresh().
    On load, entries older than 4h are treated as absent (not loaded into memory).
    """

    __tablename__ = "global_risk_snapshots"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    flagged_tickers_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
        comment="JSON array of flagged ticker strings",
    )
    verdict_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="JSON: EngineVerdict or IntelligenceEngineCompletedEvent payload",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
