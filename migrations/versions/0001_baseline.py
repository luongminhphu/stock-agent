"""Baseline schema — thay cho 57 migration cũ (gộp ngày 2026-11-11).

Revision ID: 0001_baseline
Revises: —
Create Date: 2026-11-11

Sinh bằng `alembic revision --autogenerate` từ model hiện tại trên một DB PostgreSQL trống,
sau khi gỡ các bảng không còn code dùng (trend_*, intelligence/global_risk snapshots,
engine_feedback, core_feedback, evolution_log).

DB cũ (đang ở một revision của chuỗi cũ) KHÔNG nâng cấp tại chỗ được — reset bằng
`scripts/reset_db.sh` (xoá schema public rồi `alembic upgrade head`).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "ai_interaction_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column(
            "agent_type",
            sa.String(length=64),
            nullable=False,
            comment="briefing | pretrade | replay | watchdog | thesis_review | suggest",
        ),
        sa.Column("trigger", sa.String(length=128), nullable=False),
        sa.Column(
            "tickers_json",
            sa.Text(),
            nullable=True,
            comment='JSON list of ticker symbols, e.g. \'{"VCB", "VNM"}\'',
        ),
        sa.Column(
            "ai_verdict",
            sa.String(length=32),
            nullable=True,
            comment="e.g. BULLISH / BEARISH / NEUTRAL / GO / NO_GO / HOLD",
        ),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        sa.Column(
            "ai_key_points",
            sa.Text(),
            nullable=True,
            comment="Top 3-5 insights from AI, newline-separated. Not raw JSON.",
        ),
        sa.Column(
            "ai_risk_signals",
            sa.Text(),
            nullable=True,
            comment="Risk signals highlighted by AI, newline-separated.",
        ),
        sa.Column(
            "user_signal",
            sa.String(length=32),
            nullable=True,
            comment="bought | sold | ignored | flagged | watched",
        ),
        sa.Column(
            "outcome_json",
            sa.Text(),
            nullable=True,
            comment="JSON: {price_at_signal, price_now, pct_change, thesis_status, filled_at}",
        ),
        sa.Column("thesis_id", sa.Integer(), nullable=True),
        sa.Column("decision_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ai_interaction_logs_created_at"),
        "ai_interaction_logs",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_interaction_logs_thesis_id"), "ai_interaction_logs", ["thesis_id"], unique=False
    )
    op.create_index(
        op.f("ix_ai_interaction_logs_user_id"), "ai_interaction_logs", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_ai_interaction_logs_user_signal"),
        "ai_interaction_logs",
        ["user_signal"],
        unique=False,
    )
    op.create_table(
        "brief_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tickers", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_brief_snapshots_user_id"), "brief_snapshots", ["user_id"], unique=False
    )
    op.create_index(
        "ix_brief_snapshots_user_phase_created",
        "brief_snapshots",
        ["user_id", "phase", "created_at"],
        unique=False,
    )
    op.create_table(
        "daily_agendas",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column(
            "agenda_date", sa.Date(), nullable=False, comment="Date (UTC) this agenda was built for"
        ),
        sa.Column(
            "summary",
            sa.Text(),
            nullable=False,
            comment="Compact multi-line agenda string for Discord embed prefix",
        ),
        sa.Column(
            "buckets_json",
            sa.Text(),
            nullable=True,
            comment="JSON: {decide: [...], watch: [...], defer: [...]}",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "agenda_date", name="uq_daily_agendas_user_date"),
    )
    op.create_index(op.f("ix_daily_agendas_user_id"), "daily_agendas", ["user_id"], unique=False)
    op.create_table(
        "derivative_positions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("contract_code", sa.String(length=16), nullable=False),
        sa.Column("side", sa.String(length=5), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_price", sa.Float(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=True),
        sa.Column("purpose", sa.String(length=16), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_derivative_positions_user_id"), "derivative_positions", ["user_id"], unique=False
    )
    op.create_table(
        "investor_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("behavioral_patterns", sa.Text(), nullable=False),
        sa.Column("confirmed_biases", sa.Text(), nullable=False),
        sa.Column("top_lessons", sa.Text(), nullable=False),
        sa.Column("portfolio_bias", sa.String(length=512), nullable=False),
        sa.Column("active_thesis_count", sa.Integer(), nullable=False),
        sa.Column("win_rate_30d", sa.Float(), nullable=False),
        sa.Column("avg_hold_days", sa.Float(), nullable=False),
        sa.Column("summary_for_ai", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_investor_profiles_snapshot_date"),
        "investor_profiles",
        ["snapshot_date"],
        unique=False,
    )
    op.create_table(
        "market_candle_coverage",
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("first_date", sa.Date(), nullable=False),
        sa.Column("last_date", sa.Date(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("ticker"),
    )
    op.create_table(
        "market_daily_candles",
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("ticker", "date"),
    )
    op.create_table(
        "market_quote_cache",
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("change", sa.Float(), nullable=False),
        sa.Column("change_pct", sa.Float(), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("ref_price", sa.Float(), nullable=False),
        sa.Column("ceiling", sa.Float(), nullable=False),
        sa.Column("floor", sa.Float(), nullable=False),
        sa.Column(
            "close",
            sa.Float(),
            nullable=True,
            comment="Wave C1: giá đóng cửa chính thức (ATC) do capture_eod_close ghi; NULL trong phiên",
        ),
        sa.Column(
            "quote_ts",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Timestamp of the original quote from adapter",
        ),
        sa.Column(
            "saved_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Last time this row was upserted",
        ),
        sa.PrimaryKeyConstraint("ticker"),
    )
    op.create_table(
        "memory_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "behavioral_patterns",
            sa.Text(),
            nullable=True,
            comment="Recurring decision patterns, e.g. 'FOMO khi volume breakout'",
        ),
        sa.Column(
            "cognitive_biases",
            sa.Text(),
            nullable=True,
            comment="Detected biases, e.g. 'Confirmation bias với banking sector'",
        ),
        sa.Column(
            "strengths",
            sa.Text(),
            nullable=True,
            comment="Consistent strengths, e.g. 'Kỷ luật SL khi có plan sẵn'",
        ),
        sa.Column(
            "blind_spots",
            sa.Text(),
            nullable=True,
            comment="Recurring blind spots, e.g. 'Bỏ qua macro khi local thesis quá mạnh'",
        ),
        sa.Column(
            "confidence_calibration",
            sa.Text(),
            nullable=True,
            comment="How well-calibrated confidence scores are vs actual outcomes",
        ),
        sa.Column(
            "patterns_json",
            sa.Text(),
            nullable=True,
            comment="JSON list of SemanticPattern dicts for downstream prompt injection",
        ),
        sa.Column("episode_count", sa.Integer(), nullable=False),
        sa.Column("verdict_accuracy", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_synthesis_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp of the last successful synthesize_patterns() call for this user",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_memory_snapshots_created_at"), "memory_snapshots", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_memory_snapshots_user_id"), "memory_snapshots", ["user_id"], unique=False
    )
    op.create_table(
        "position_daily_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("avg_cost", sa.Float(), nullable=False),
        sa.Column("close_price", sa.Float(), nullable=False),
        sa.Column("cost_basis", sa.Float(), nullable=False),
        sa.Column("market_value", sa.Float(), nullable=False),
        sa.Column("unrealized_pnl", sa.Float(), nullable=False),
        sa.Column("unrealized_pct", sa.Float(), nullable=False),
        sa.Column("thesis_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "ticker", "snapshot_date", name="uq_position_daily_snapshot"
        ),
    )
    op.create_index(
        op.f("ix_position_daily_snapshots_snapshot_date"),
        "position_daily_snapshots",
        ["snapshot_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_position_daily_snapshots_ticker"),
        "position_daily_snapshots",
        ["ticker"],
        unique=False,
    )
    op.create_index(
        op.f("ix_position_daily_snapshots_user_id"),
        "position_daily_snapshots",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("avg_cost", sa.Float(), nullable=False),
        sa.Column("sector", sa.String(length=64), nullable=True),
        sa.Column("thesis_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("locked_qty", sa.Float(), server_default="0", nullable=False),
        sa.Column("locked_reason", sa.String(length=64), nullable=True),
        sa.Column("locked_until", sa.Date(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_positions_thesis_id"), "positions", ["thesis_id"], unique=False)
    op.create_index(op.f("ix_positions_user_id"), "positions", ["user_id"], unique=False)
    op.create_table(
        "signal_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column("signal_type", sa.String(length=64), nullable=False),
        sa.Column("strength", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index(
        op.f("ix_signal_events_signal_type"), "signal_events", ["signal_type"], unique=False
    )
    op.create_index(op.f("ix_signal_events_ticker"), "signal_events", ["ticker"], unique=False)
    op.create_index(op.f("ix_signal_events_user_id"), "signal_events", ["user_id"], unique=False)
    op.create_index(
        "ix_signal_events_user_occurred", "signal_events", ["user_id", "occurred_at"], unique=False
    )
    op.create_index(
        "ix_signal_events_user_ticker_occurred",
        "signal_events",
        ["user_id", "ticker", "occurred_at"],
        unique=False,
    )
    op.create_table(
        "theses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("active", "invalidated", "closed", "paused", "weakening", name="thesisstatus"),
            nullable=False,
        ),
        sa.Column(
            "direction",
            sa.Enum("BULLISH", "BEARISH", "NEUTRAL", name="thesisdirection"),
            nullable=True,
        ),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("target_price", sa.Float(), nullable=True),
        sa.Column("stop_loss", sa.Float(), nullable=True),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("actual_entry_price", sa.Float(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_judged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_by", sa.Date(), nullable=True),
        sa.Column("plan_light", sa.String(length=8), nullable=True),
        sa.Column("plan_summary", sa.Text(), nullable=True),
        sa.Column("plan_reasons", sa.Text(), nullable=True),
        sa.Column("plan_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_theses_created_at"), "theses", ["created_at"], unique=False)
    op.create_index(
        op.f("ix_theses_last_reviewed_at"), "theses", ["last_reviewed_at"], unique=False
    )
    op.create_index(op.f("ix_theses_plan_light"), "theses", ["plan_light"], unique=False)
    op.create_index(op.f("ix_theses_status"), "theses", ["status"], unique=False)
    op.create_index(op.f("ix_theses_ticker"), "theses", ["ticker"], unique=False)
    op.create_index(op.f("ix_theses_user_id"), "theses", ["user_id"], unique=False)
    op.create_table(
        "user_behavior_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column(
            "signal",
            sa.String(length=32),
            nullable=False,
            comment="bought | sold | watched | ignored | flagged | engine:<outcome> | brief:<outcome> | followed_advice | ignored_advice",
        ),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            comment="discord_reaction | command | api | feedback_listener | core | briefing | thesis",
        ),
        sa.Column(
            "interaction_log_id",
            sa.Integer(),
            nullable=True,
            comment="FK to AIInteractionLog.id — null if signal has no AI source",
        ),
        sa.Column("ticker", sa.String(length=16), nullable=True),
        sa.Column("agent_type", sa.String(length=64), nullable=True),
        sa.Column("note", sa.String(length=512), nullable=True),
        sa.Column(
            "ref_type",
            sa.String(length=16),
            nullable=True,
            comment="verdict | brief | pretrade | thesis",
        ),
        sa.Column(
            "ref_id",
            sa.String(length=64),
            nullable=True,
            comment="verdict_event_id | brief_snapshot_id | decision_log_id | thesis_id",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_user_behavior_logs_created_at"), "user_behavior_logs", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_user_behavior_logs_interaction_log_id"),
        "user_behavior_logs",
        ["interaction_log_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_behavior_logs_ref", "user_behavior_logs", ["ref_type", "ref_id"], unique=False
    )
    op.create_index(
        op.f("ix_user_behavior_logs_ticker"), "user_behavior_logs", ["ticker"], unique=False
    )
    op.create_index(
        op.f("ix_user_behavior_logs_user_id"), "user_behavior_logs", ["user_id"], unique=False
    )
    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("thesis_id", sa.Integer(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "ticker", name="uq_watchlist_user_ticker"),
    )
    op.create_index(
        op.f("ix_watchlist_items_thesis_id"), "watchlist_items", ["thesis_id"], unique=False
    )
    op.create_index(
        op.f("ix_watchlist_items_user_id"), "watchlist_items", ["user_id"], unique=False
    )
    op.create_table(
        "watchlist_scans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_watchlist_scans_user_id"), "watchlist_scans", ["user_id"], unique=False
    )
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column("watchlist_item_id", sa.Integer(), nullable=True),
        sa.Column(
            "condition_type",
            sa.Enum(
                "price_above",
                "price_below",
                "change_pct_up",
                "change_pct_down",
                "volume_spike",
                "thesis_trigger",
                name="alertconditiontype",
            ),
            nullable=False,
        ),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "triggered", "dismissed", "expired", name="alertstatus"),
            nullable=False,
        ),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_price", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("auto_reactivate", sa.Boolean(), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=True),
        sa.Column("thesis_id", sa.String(length=64), nullable=True),
        sa.Column("dedup_key", sa.String(length=128), nullable=True),
        sa.Column("source_event_id", sa.String(length=64), nullable=True),
        sa.Column("priority", sa.String(length=16), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("reaction_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("dismiss_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("effective_cooldown_hours", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["watchlist_item_id"], ["watchlist_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_alerts_dedup_key"), "alerts", ["dedup_key"], unique=False)
    op.create_index(op.f("ix_alerts_thesis_id"), "alerts", ["thesis_id"], unique=False)
    op.create_index(op.f("ix_alerts_user_id"), "alerts", ["user_id"], unique=False)
    op.create_index(
        "ix_alerts_user_status_triggered",
        "alerts",
        ["user_id", "status", "triggered_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_alerts_watchlist_item_id"), "alerts", ["watchlist_item_id"], unique=False
    )
    op.create_table(
        "assumptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thesis_id", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "valid",
                "invalid",
                "uncertain",
                "pending",
                "needs_monitoring",
                name="assumptionstatus",
            ),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("check_key", sa.String(length=32), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["thesis_id"], ["theses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_assumptions_status"), "assumptions", ["status"], unique=False)
    op.create_index(op.f("ix_assumptions_thesis_id"), "assumptions", ["thesis_id"], unique=False)
    op.create_table(
        "brief_feedback",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("brief_snapshot_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["brief_snapshot_id"], ["brief_snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_brief_feedback_brief_snapshot_id"),
        "brief_feedback",
        ["brief_snapshot_id"],
        unique=False,
    )
    op.create_index(
        "ix_brief_feedback_snapshot_user",
        "brief_feedback",
        ["brief_snapshot_id", "user_id"],
        unique=False,
    )
    op.create_index(op.f("ix_brief_feedback_user_id"), "brief_feedback", ["user_id"], unique=False)
    op.create_table(
        "catalysts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thesis_id", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("expected_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "triggered", "expired", "cancelled", name="catalyststatus"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["thesis_id"], ["theses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_catalysts_status"), "catalysts", ["status"], unique=False)
    op.create_index(op.f("ix_catalysts_thesis_id"), "catalysts", ["thesis_id"], unique=False)
    op.create_table(
        "decision_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thesis_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column(
            "decision_type",
            sa.Enum("BUY", "SELL", "HOLD", "ADD", "REDUCE", "PRETRADE_ADVICE", name="decisiontype"),
            nullable=False,
        ),
        sa.Column(
            "decision_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("price_at_decision", sa.Float(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("thesis_score_at_decision", sa.Float(), nullable=True),
        sa.Column("thesis_health_score_at_decision", sa.Integer(), nullable=True),
        sa.Column("active_signal", sa.String(length=100), nullable=True),
        sa.Column("brief_summary", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("review_horizon_days", sa.Integer(), nullable=False),
        sa.Column("outcome_price", sa.Float(), nullable=True),
        sa.Column("outcome_pnl_pct", sa.Float(), nullable=True),
        sa.Column("outcome_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "outcome_verdict",
            sa.Enum("CORRECT", "INCORRECT", "MIXED", name="outcomeverdict"),
            nullable=True,
        ),
        sa.Column("key_lesson", sa.Text(), nullable=True),
        sa.Column("pattern_detected", sa.String(length=100), nullable=True),
        sa.Column("advice_id", sa.Integer(), nullable=True),
        sa.Column("advice_action", sa.String(length=10), nullable=True),
        sa.Column("advice_proceed", sa.Boolean(), nullable=True),
        sa.Column("adherence", sa.String(length=16), nullable=True),
        sa.Column("adherence_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["advice_id"], ["decision_logs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["thesis_id"], ["theses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_decision_logs_advice_id"), "decision_logs", ["advice_id"], unique=False
    )
    op.create_index(
        op.f("ix_decision_logs_decision_at"), "decision_logs", ["decision_at"], unique=False
    )
    op.create_index(
        op.f("ix_decision_logs_decision_type"), "decision_logs", ["decision_type"], unique=False
    )
    op.create_index(
        op.f("ix_decision_logs_outcome_evaluated_at"),
        "decision_logs",
        ["outcome_evaluated_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_decision_logs_outcome_verdict"), "decision_logs", ["outcome_verdict"], unique=False
    )
    op.create_index(
        op.f("ix_decision_logs_thesis_id"), "decision_logs", ["thesis_id"], unique=False
    )
    op.create_index(op.f("ix_decision_logs_ticker"), "decision_logs", ["ticker"], unique=False)
    op.create_index(op.f("ix_decision_logs_user_id"), "decision_logs", ["user_id"], unique=False)
    op.create_table(
        "dividend_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column("position_id", sa.Integer(), nullable=True),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("dividend_per_share", sa.Float(), nullable=False),
        sa.Column("total_amount", sa.Float(), nullable=False),
        sa.Column("dividend_type", sa.Enum("cash", "stock", name="dividendtype"), nullable=False),
        sa.Column("ex_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["position_id"], ["positions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_dividend_records_position_id"), "dividend_records", ["position_id"], unique=False
    )
    op.create_index(
        op.f("ix_dividend_records_ticker"), "dividend_records", ["ticker"], unique=False
    )
    op.create_index(
        op.f("ix_dividend_records_user_id"), "dividend_records", ["user_id"], unique=False
    )
    op.create_table(
        "position_edits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column("position_id", sa.Integer(), nullable=False),
        sa.Column("old_qty", sa.Float(), nullable=False),
        sa.Column("new_qty", sa.Float(), nullable=False),
        sa.Column("old_avg_cost", sa.Float(), nullable=False),
        sa.Column("new_avg_cost", sa.Float(), nullable=False),
        sa.Column("old_locked_qty", sa.Float(), nullable=True),
        sa.Column("new_locked_qty", sa.Float(), nullable=True),
        sa.Column("old_locked_reason", sa.String(length=64), nullable=True),
        sa.Column("new_locked_reason", sa.String(length=64), nullable=True),
        sa.Column("old_locked_until", sa.Date(), nullable=True),
        sa.Column("new_locked_until", sa.Date(), nullable=True),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["position_id"],
            ["positions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_position_edits_user_id"), "position_edits", ["user_id"], unique=False)
    op.create_table(
        "reminders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("watchlist_item_id", sa.Integer(), nullable=False),
        sa.Column(
            "frequency",
            sa.Enum("daily", "weekly", "on_signal", name="reminderfrequency"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["watchlist_item_id"], ["watchlist_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_reminders_user_id"), "reminders", ["user_id"], unique=False)
    op.create_table(
        "thesis_reviews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thesis_id", sa.Integer(), nullable=False),
        sa.Column(
            "verdict",
            sa.Enum(
                "BULLISH",
                "BEARISH",
                "NEUTRAL",
                "WATCHLIST",
                "WEAKENING",
                "INVALIDATED",
                "INSUFFICIENT_DATA",
                name="reviewverdict",
            ),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("risk_signals", sa.Text(), nullable=True),
        sa.Column("next_watch_items", sa.Text(), nullable=True),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("reviewed_price", sa.Float(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["thesis_id"], ["theses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_thesis_reviews_reviewed_at"), "thesis_reviews", ["reviewed_at"], unique=False
    )
    op.create_index(
        op.f("ix_thesis_reviews_thesis_id"), "thesis_reviews", ["thesis_id"], unique=False
    )
    op.create_index(op.f("ix_thesis_reviews_verdict"), "thesis_reviews", ["verdict"], unique=False)
    op.create_table(
        "thesis_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thesis_id", sa.Integer(), nullable=False),
        sa.Column("price_at_snapshot", sa.Float(), nullable=True),
        sa.Column("pnl_pct", sa.Float(), nullable=True),
        sa.Column("score_at_snapshot", sa.Float(), nullable=True),
        sa.Column(
            "snapshotted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("verdict", sa.String(length=32), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("score_breakdown", sa.Text(), nullable=True),
        sa.Column("conviction_score", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["thesis_id"], ["theses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_thesis_snapshots_snapshotted_at"),
        "thesis_snapshots",
        ["snapshotted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_thesis_snapshots_thesis_id"), "thesis_snapshots", ["thesis_id"], unique=False
    )
    op.create_table(
        "trades",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column("position_id", sa.Integer(), nullable=False),
        sa.Column("trade_type", sa.Enum("buy", "sell", "adjust", name="tradetype"), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("realized_pnl", sa.Float(), nullable=True),
        sa.Column("traded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "exit_reason",
            sa.Enum(
                "thesis_invalidated",
                "target_hit",
                "stop_loss",
                "time_decay",
                "opportunity_cost",
                "manual",
                name="exitreason",
            ),
            nullable=True,
            comment="SELL only — why position was closed. See ExitReason enum.",
        ),
        sa.Column(
            "entry_signal_ref",
            sa.String(length=64),
            nullable=True,
            comment="BUY only — optional ref to brief/signal that triggered entry.",
        ),
        sa.ForeignKeyConstraint(["position_id"], ["positions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_trades_position_id"), "trades", ["position_id"], unique=False)
    op.create_index(op.f("ix_trades_user_id"), "trades", ["user_id"], unique=False)
    op.create_table(
        "review_recommendations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("review_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "accepted", "rejected", name="recommendationstatus"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("target_type", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("target_description", sa.Text(), nullable=True),
        sa.Column("recommended_status", sa.String(length=32), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("acted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_timeline", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["review_id"], ["thesis_reviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_review_recommendations_review_id"),
        "review_recommendations",
        ["review_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_review_recommendations_status"), "review_recommendations", ["status"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_review_recommendations_status"), table_name="review_recommendations")
    op.drop_index(op.f("ix_review_recommendations_review_id"), table_name="review_recommendations")
    op.drop_table("review_recommendations")
    op.drop_index(op.f("ix_trades_user_id"), table_name="trades")
    op.drop_index(op.f("ix_trades_position_id"), table_name="trades")
    op.drop_table("trades")
    op.drop_index(op.f("ix_thesis_snapshots_thesis_id"), table_name="thesis_snapshots")
    op.drop_index(op.f("ix_thesis_snapshots_snapshotted_at"), table_name="thesis_snapshots")
    op.drop_table("thesis_snapshots")
    op.drop_index(op.f("ix_thesis_reviews_verdict"), table_name="thesis_reviews")
    op.drop_index(op.f("ix_thesis_reviews_thesis_id"), table_name="thesis_reviews")
    op.drop_index(op.f("ix_thesis_reviews_reviewed_at"), table_name="thesis_reviews")
    op.drop_table("thesis_reviews")
    op.drop_index(op.f("ix_reminders_user_id"), table_name="reminders")
    op.drop_table("reminders")
    op.drop_index(op.f("ix_position_edits_user_id"), table_name="position_edits")
    op.drop_table("position_edits")
    op.drop_index(op.f("ix_dividend_records_user_id"), table_name="dividend_records")
    op.drop_index(op.f("ix_dividend_records_ticker"), table_name="dividend_records")
    op.drop_index(op.f("ix_dividend_records_position_id"), table_name="dividend_records")
    op.drop_table("dividend_records")
    op.drop_index(op.f("ix_decision_logs_user_id"), table_name="decision_logs")
    op.drop_index(op.f("ix_decision_logs_ticker"), table_name="decision_logs")
    op.drop_index(op.f("ix_decision_logs_thesis_id"), table_name="decision_logs")
    op.drop_index(op.f("ix_decision_logs_outcome_verdict"), table_name="decision_logs")
    op.drop_index(op.f("ix_decision_logs_outcome_evaluated_at"), table_name="decision_logs")
    op.drop_index(op.f("ix_decision_logs_decision_type"), table_name="decision_logs")
    op.drop_index(op.f("ix_decision_logs_decision_at"), table_name="decision_logs")
    op.drop_index(op.f("ix_decision_logs_advice_id"), table_name="decision_logs")
    op.drop_table("decision_logs")
    op.drop_index(op.f("ix_catalysts_thesis_id"), table_name="catalysts")
    op.drop_index(op.f("ix_catalysts_status"), table_name="catalysts")
    op.drop_table("catalysts")
    op.drop_index(op.f("ix_brief_feedback_user_id"), table_name="brief_feedback")
    op.drop_index("ix_brief_feedback_snapshot_user", table_name="brief_feedback")
    op.drop_index(op.f("ix_brief_feedback_brief_snapshot_id"), table_name="brief_feedback")
    op.drop_table("brief_feedback")
    op.drop_index(op.f("ix_assumptions_thesis_id"), table_name="assumptions")
    op.drop_index(op.f("ix_assumptions_status"), table_name="assumptions")
    op.drop_table("assumptions")
    op.drop_index(op.f("ix_alerts_watchlist_item_id"), table_name="alerts")
    op.drop_index("ix_alerts_user_status_triggered", table_name="alerts")
    op.drop_index(op.f("ix_alerts_user_id"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_thesis_id"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_dedup_key"), table_name="alerts")
    op.drop_table("alerts")
    op.drop_index(op.f("ix_watchlist_scans_user_id"), table_name="watchlist_scans")
    op.drop_table("watchlist_scans")
    op.drop_index(op.f("ix_watchlist_items_user_id"), table_name="watchlist_items")
    op.drop_index(op.f("ix_watchlist_items_thesis_id"), table_name="watchlist_items")
    op.drop_table("watchlist_items")
    op.drop_index(op.f("ix_user_behavior_logs_user_id"), table_name="user_behavior_logs")
    op.drop_index(op.f("ix_user_behavior_logs_ticker"), table_name="user_behavior_logs")
    op.drop_index("ix_user_behavior_logs_ref", table_name="user_behavior_logs")
    op.drop_index(op.f("ix_user_behavior_logs_interaction_log_id"), table_name="user_behavior_logs")
    op.drop_index(op.f("ix_user_behavior_logs_created_at"), table_name="user_behavior_logs")
    op.drop_table("user_behavior_logs")
    op.drop_index(op.f("ix_theses_user_id"), table_name="theses")
    op.drop_index(op.f("ix_theses_ticker"), table_name="theses")
    op.drop_index(op.f("ix_theses_status"), table_name="theses")
    op.drop_index(op.f("ix_theses_plan_light"), table_name="theses")
    op.drop_index(op.f("ix_theses_last_reviewed_at"), table_name="theses")
    op.drop_index(op.f("ix_theses_created_at"), table_name="theses")
    op.drop_table("theses")
    op.drop_index("ix_signal_events_user_ticker_occurred", table_name="signal_events")
    op.drop_index("ix_signal_events_user_occurred", table_name="signal_events")
    op.drop_index(op.f("ix_signal_events_user_id"), table_name="signal_events")
    op.drop_index(op.f("ix_signal_events_ticker"), table_name="signal_events")
    op.drop_index(op.f("ix_signal_events_signal_type"), table_name="signal_events")
    op.drop_table("signal_events")
    op.drop_index(op.f("ix_positions_user_id"), table_name="positions")
    op.drop_index(op.f("ix_positions_thesis_id"), table_name="positions")
    op.drop_table("positions")
    op.drop_index(
        op.f("ix_position_daily_snapshots_user_id"), table_name="position_daily_snapshots"
    )
    op.drop_index(op.f("ix_position_daily_snapshots_ticker"), table_name="position_daily_snapshots")
    op.drop_index(
        op.f("ix_position_daily_snapshots_snapshot_date"), table_name="position_daily_snapshots"
    )
    op.drop_table("position_daily_snapshots")
    op.drop_index(op.f("ix_memory_snapshots_user_id"), table_name="memory_snapshots")
    op.drop_index(op.f("ix_memory_snapshots_created_at"), table_name="memory_snapshots")
    op.drop_table("memory_snapshots")
    op.drop_table("market_quote_cache")
    op.drop_table("market_daily_candles")
    op.drop_table("market_candle_coverage")
    op.drop_index(op.f("ix_investor_profiles_snapshot_date"), table_name="investor_profiles")
    op.drop_table("investor_profiles")
    op.drop_index(op.f("ix_derivative_positions_user_id"), table_name="derivative_positions")
    op.drop_table("derivative_positions")
    op.drop_index(op.f("ix_daily_agendas_user_id"), table_name="daily_agendas")
    op.drop_table("daily_agendas")
    op.drop_index("ix_brief_snapshots_user_phase_created", table_name="brief_snapshots")
    op.drop_index(op.f("ix_brief_snapshots_user_id"), table_name="brief_snapshots")
    op.drop_table("brief_snapshots")
    op.drop_index(op.f("ix_ai_interaction_logs_user_signal"), table_name="ai_interaction_logs")
    op.drop_index(op.f("ix_ai_interaction_logs_user_id"), table_name="ai_interaction_logs")
    op.drop_index(op.f("ix_ai_interaction_logs_thesis_id"), table_name="ai_interaction_logs")
    op.drop_index(op.f("ix_ai_interaction_logs_created_at"), table_name="ai_interaction_logs")
    op.drop_table("ai_interaction_logs")

    # Kiểu ENUM của PostgreSQL không bị xoá cùng bảng — xoá tường minh để downgrade/upgrade lặp lại được.
    bind = op.get_bind()
    for name in (
        "alertconditiontype",
        "alertstatus",
        "assumptionstatus",
        "catalyststatus",
        "decisiontype",
        "dividendtype",
        "exitreason",
        "outcomeverdict",
        "recommendationstatus",
        "reminderfrequency",
        "reviewverdict",
        "thesisdirection",
        "thesisstatus",
        "tradetype",
    ):
        sa.Enum(name=name).drop(bind, checkfirst=True)
