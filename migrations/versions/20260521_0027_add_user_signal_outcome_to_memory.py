"""Add user_signal, outcome_json to ai_interaction_logs;
add patterns_json to memory_snapshots.

Revision ID: 20260521_0027
Revises: 0026
Create Date: 2026-05-21

ai_interaction_logs:
  - user_signal   VARCHAR(32) NULL  — bot reaction: bought/sold/ignored/flagged/watched
  - outcome_json  TEXT        NULL  — filled by scheduler after N days:
                                      {"price_at_signal": 0, "price_now": 0,
                                       "pct_change": 0, "thesis_status": "holding",
                                       "filled_at": "..."}

memory_snapshots:
  - patterns_json TEXT NULL  — structured list of SemanticPattern objects for prompt injection
                               [{"pattern_type": "...", "description": "...", "confidence": 0.8}]
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260521_0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # ai_interaction_logs — episodic layer: user signal + price outcome
    # ------------------------------------------------------------------
    op.add_column(
        "ai_interaction_logs",
        sa.Column("user_signal", sa.String(32), nullable=True,
                  comment="bought | sold | ignored | flagged | watched"),
    )
    op.add_column(
        "ai_interaction_logs",
        sa.Column("outcome_json", sa.Text(), nullable=True,
                  comment="JSON: price_at_signal, price_now, pct_change, thesis_status, filled_at"),
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_interaction_logs_user_signal "
        "ON ai_interaction_logs (user_signal)"
    )
    # Partial index: quickly find episodes pending outcome fill
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_interaction_logs_outcome_pending "
        "ON ai_interaction_logs (created_at) WHERE outcome_json IS NULL"
    )

    # ------------------------------------------------------------------
    # memory_snapshots — semantic layer: structured patterns for prompt injection
    # ------------------------------------------------------------------
    op.add_column(
        "memory_snapshots",
        sa.Column("patterns_json", sa.Text(), nullable=True,
                  comment="JSON list of SemanticPattern: [{pattern_type, description, confidence}]"),
    )


def downgrade() -> None:
    op.drop_index("ix_ai_interaction_logs_outcome_pending", table_name="ai_interaction_logs")
    op.drop_index("ix_ai_interaction_logs_user_signal", table_name="ai_interaction_logs")
    op.drop_column("ai_interaction_logs", "outcome_json")
    op.drop_column("ai_interaction_logs", "user_signal")
    op.drop_column("memory_snapshots", "patterns_json")
