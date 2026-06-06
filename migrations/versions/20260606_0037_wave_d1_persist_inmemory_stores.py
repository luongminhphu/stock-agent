"""Wave D.1 — Persist critical in-memory stores to DB.

Revision ID: 20260606_0037
Revises: 20260604_0036
Create Date: 2026-06-06

Problem:
    Five in-memory stores lose their state on every app/bot restart:
      1. TrendSnapshotStore  — TrendShiftDetector loses baseline for shift detection
      2. TrendPredictionStore — briefing, bot /trend, API /trend serve empty response
      3. IntelligenceSnapshotStore — dashboard intelligence panel returns 204
      4. GlobalRiskStore — BriefingService/ScanService/ThesisReviewService lose risk signal
      5. AgendaCache — morning agenda buckets (decide/watch/defer) lost mid-day

New tables:
    trend_snapshots         — symbol PK, bundle_json, saved_at
    trend_predictions       — symbol PK, verdict, confidence, reasoning_json, predicted_at, expires_at
    intelligence_snapshots  — user_id PK, report_json, trigger_source, captured_at
    global_risk_snapshots   — user_id PK, flagged_tickers_json, verdict_json, updated_at
    daily_agendas           — (user_id, agenda_date) composite PK, summary, buckets_json

All stores use upsert pattern (ON CONFLICT DO UPDATE) — one active row per key.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260606_0037"
down_revision = "20260604_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. trend_snapshots
    # ------------------------------------------------------------------
    op.create_table(
        "trend_snapshots",
        sa.Column("symbol", sa.String(20), primary_key=True, nullable=False),
        sa.Column("bundle_json", sa.Text(), nullable=False,
                  comment="JSON: TechnicalSignalBundle.model_dump()"),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )

    # ------------------------------------------------------------------
    # 2. trend_predictions
    # ------------------------------------------------------------------
    op.create_table(
        "trend_predictions",
        sa.Column("symbol", sa.String(20), primary_key=True, nullable=False),
        sa.Column("verdict", sa.String(32), nullable=False,
                  comment="STRONG_BUY | BUY | WATCH | HOLD | REDUCE | STRONG_SELL"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reasoning_json", sa.Text(), nullable=True,
                  comment="JSON: full TrendPrediction.model_dump() for warm restore"),
        sa.Column("predicted_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False,
                  comment="predicted_at + 4h — filter on load to skip stale predictions"),
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_trend_predictions_expires_at "
        "ON trend_predictions (expires_at)"
    )

    # ------------------------------------------------------------------
    # 3. intelligence_snapshots
    # ------------------------------------------------------------------
    op.create_table(
        "intelligence_snapshots",
        sa.Column("user_id", sa.String(64), primary_key=True, nullable=False),
        sa.Column("report_json", sa.Text(), nullable=False,
                  comment="JSON: IntelligenceReport.model_dump()"),
        sa.Column("trigger_source", sa.String(32), nullable=False,
                  server_default="unknown"),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )

    # ------------------------------------------------------------------
    # 4. global_risk_snapshots
    # ------------------------------------------------------------------
    op.create_table(
        "global_risk_snapshots",
        sa.Column("user_id", sa.String(64), primary_key=True, nullable=False),
        sa.Column("flagged_tickers_json", sa.Text(), nullable=False,
                  server_default="'[]'",
                  comment="JSON array of flagged ticker strings"),
        sa.Column("verdict_json", sa.Text(), nullable=True,
                  comment="JSON: EngineVerdict or IntelligenceEngineCompletedEvent payload"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )

    # ------------------------------------------------------------------
    # 5. daily_agendas
    # ------------------------------------------------------------------
    op.create_table(
        "daily_agendas",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("agenda_date", sa.Date(), nullable=False,
                  comment="Date (UTC) this agenda was built for"),
        sa.Column("summary", sa.Text(), nullable=False,
                  comment="Compact multi-line agenda string for Discord embed prefix"),
        sa.Column("buckets_json", sa.Text(), nullable=True,
                  comment="JSON: {decide: [...], watch: [...], defer: [...]}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("user_id", "agenda_date", name="uq_daily_agendas_user_date"),
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_daily_agendas_user_id "
        "ON daily_agendas (user_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_daily_agendas_user_id")
    op.drop_table("daily_agendas")
    op.drop_table("global_risk_snapshots")
    op.drop_table("intelligence_snapshots")
    op.execute("DROP INDEX IF EXISTS ix_trend_predictions_expires_at")
    op.drop_table("trend_predictions")
    op.drop_table("trend_snapshots")
