"""ORM models cho Wave D.1 — persisted in-memory stores.

Owner: readmodel segment.

Tables:
    trend_snapshots         — TrendSnapshotStore (market/readmodel)
    trend_predictions       — readmodel.TrendPredictionStore
    intelligence_snapshots  — IntelligenceSnapshotStore
    global_risk_snapshots   — GlobalRiskStore
    daily_agendas           — AgendaCache (briefing)

Tất cả các bảng đều dùng upsert pattern (ON CONFLICT DO UPDATE)
để keep it simple — mỗi symbol/user chỉ có 1 row hiện tại.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    ARRAY,
    Date,
    DateTime,
    Float,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.db import Base


class TrendSnapshot(Base):
    """Persisted TechnicalSignalBundle per symbol — TrendSnapshotStore Wave 2.

    Survives bot restarts so TrendShiftDetector always has a baseline to compare
    against instead of treating every post-restart cycle as cold start.

    PK: symbol (one row per symbol, upserted on every save()).
    """

    __tablename__ = "trend_snapshots"

    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    bundle_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="JSON: TechnicalSignalBundle.model_dump()",
    )
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class TrendPrediction(Base):
    """Persisted TrendPrediction per symbol — readmodel.TrendPredictionStore Wave 2.

    Allows briefing, bot /trend, and API /trend to serve the last known
    prediction after a restart without re-running the AI engine.

    expires_at: prediction is treated as absent after this timestamp (4h TTL).
    """

    __tablename__ = "trend_predictions"

    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    verdict: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="STRONG_BUY | BUY | WATCH | HOLD | REDUCE | STRONG_SELL",
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reasoning_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="JSON: full TrendPrediction.model_dump() for warm restore",
    )
    predicted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="predicted_at + 4h — filter on load to skip stale predictions",
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


class DailyAgenda(Base):
    """Persisted CachedAgenda per user per date — AgendaCache.

    Scoped by date so we never restore yesterday's agenda.
    On load: only restore if date == today (Asia/Bangkok).

    PK: (user_id, date) — one agenda per user per day.
    """

    __tablename__ = "daily_agendas"
    __table_args__ = (UniqueConstraint("user_id", "agenda_date", name="uq_daily_agendas_user_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    agenda_date: Mapped[datetime] = mapped_column(
        Date,
        nullable=False,
        comment="Date (UTC) this agenda was built for",
    )
    summary: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Compact multi-line agenda string for Discord embed prefix",
    )
    buckets_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="JSON: {decide: [...], watch: [...], defer: [...]}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
