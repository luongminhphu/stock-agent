"""Add decision_logs table.

Revision ID: 20260504_0009
Revises: 20260504_0008
Create Date: 2026-05-04

Adds the decision_logs table which backs DecisionLog ORM model in
src/thesis/models.py. Includes all fields required by the Decision
Replay loop:

  - Core decision fields (user_id, ticker, decision_type, rationale, horizon)
  - Frozen context at decision time (price, thesis_score, thesis_health_score,
    active_signal, brief_summary)
  - Outcome evaluation fields (outcome_price, outcome_pnl_pct,
    outcome_evaluated_at, outcome_verdict)
  - AI lesson fields (key_lesson, pattern_detected) — written by
    DecisionService.persist_lesson() after ReplayAgent analysis
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260504_0009"
down_revision: str = "20260504_0008"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Postgres enum types — created once; no-op on SQLite.
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE decisiontype AS ENUM ('BUY', 'SELL', 'HOLD', 'ADD', 'REDUCE');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE outcomeoverdict AS ENUM ('CORRECT', 'INCORRECT', 'MIXED');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )

    op.create_table(
        "decision_logs",
        # --- identity ---
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thesis_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),

        # --- decision ---
        sa.Column(
            "decision_type",
            sa.Enum("BUY", "SELL", "HOLD", "ADD", "REDUCE", name="decisiontype"),
            nullable=False,
        ),
        sa.Column(
            "decision_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("review_horizon_days", sa.Integer(), nullable=False, server_default="30"),

        # --- frozen context at decision time ---
        sa.Column("price_at_decision", sa.Float(), nullable=True),
        sa.Column("thesis_score_at_decision", sa.Float(), nullable=True),
        sa.Column("thesis_health_score_at_decision", sa.Integer(), nullable=True),
        sa.Column("active_signal", sa.String(100), nullable=True),
        sa.Column("brief_summary", sa.Text(), nullable=True),

        # --- outcome evaluation (filled later by DecisionService.evaluate_outcome) ---
        sa.Column("outcome_price", sa.Float(), nullable=True),
        sa.Column("outcome_pnl_pct", sa.Float(), nullable=True),
        sa.Column("outcome_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "outcome_verdict",
            sa.Enum("CORRECT", "INCORRECT", "MIXED", name="outcomeoverdict"),
            nullable=True,
        ),

        # --- AI lessons (filled by DecisionService.persist_lesson after ReplayAgent) ---
        sa.Column("key_lesson", sa.Text(), nullable=True),
        sa.Column("pattern_detected", sa.String(100), nullable=True),

        # --- constraints ---
        sa.ForeignKeyConstraint(["thesis_id"], ["theses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_decision_logs_user_id", "decision_logs", ["user_id"])
    op.create_index("ix_decision_logs_ticker", "decision_logs", ["ticker"])
    op.create_index("ix_decision_logs_thesis_id", "decision_logs", ["thesis_id"])
    op.create_index("ix_decision_logs_decision_at", "decision_logs", ["decision_at"])
    op.create_index(
        "ix_decision_logs_outcome_evaluated_at",
        "decision_logs",
        ["outcome_evaluated_at"],
    )
    op.create_index(
        "ix_decision_logs_outcome_verdict",
        "decision_logs",
        ["outcome_verdict"],
    )


def downgrade() -> None:
    op.drop_index("ix_decision_logs_outcome_verdict", table_name="decision_logs")
    op.drop_index("ix_decision_logs_outcome_evaluated_at", table_name="decision_logs")
    op.drop_index("ix_decision_logs_decision_at", table_name="decision_logs")
    op.drop_index("ix_decision_logs_thesis_id", table_name="decision_logs")
    op.drop_index("ix_decision_logs_ticker", table_name="decision_logs")
    op.drop_index("ix_decision_logs_user_id", table_name="decision_logs")
    op.drop_table("decision_logs")
    op.execute("DROP TYPE IF EXISTS outcomeoverdict")
    op.execute("DROP TYPE IF EXISTS decisiontype")
