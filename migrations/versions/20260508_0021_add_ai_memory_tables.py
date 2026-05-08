"""Add ai_interaction_logs, memory_snapshots, investor_profiles tables.

Revision ID: 20260508_0021
Revises: 20260508_0020
Create Date: 2026-05-08

These tables were previously defined in 0006_ai_memory_tables.sql (raw SQL,
not tracked by Alembic) and in migration 0014 (investor_profiles). This
migration formalises all three into the Alembic chain so fresh installs
create them correctly.

All CREATE TABLE / CREATE INDEX use IF NOT EXISTS so the migration is
safe to run against a DB that already has the tables.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260508_0021"
down_revision = "20260508_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # ai_interaction_logs  (ai segment — episodic memory layer 2)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS ai_interaction_logs (
            id               SERIAL PRIMARY KEY,
            user_id          VARCHAR(64)  NOT NULL,
            agent_type       VARCHAR(64)  NOT NULL,
            trigger          VARCHAR(128) NOT NULL DEFAULT 'unknown',
            tickers_json     TEXT,
            ai_verdict       VARCHAR(32),
            ai_confidence    FLOAT,
            ai_key_points    TEXT,
            ai_risk_signals  TEXT,
            thesis_id        INTEGER,
            decision_id      INTEGER,
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_ai_interaction_logs_user_id    ON ai_interaction_logs (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ai_interaction_logs_thesis_id  ON ai_interaction_logs (thesis_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ai_interaction_logs_created_at ON ai_interaction_logs (created_at)")

    # ------------------------------------------------------------------
    # memory_snapshots  (ai segment — semantic memory layer 3)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS memory_snapshots (
            id                     SERIAL PRIMARY KEY,
            user_id                VARCHAR(64)  NOT NULL,
            period_start           TIMESTAMPTZ  NOT NULL,
            period_end             TIMESTAMPTZ  NOT NULL,
            behavioral_patterns    TEXT,
            cognitive_biases       TEXT,
            strengths              TEXT,
            blind_spots            TEXT,
            confidence_calibration TEXT,
            episode_count          INTEGER      NOT NULL DEFAULT 0,
            verdict_accuracy       FLOAT,
            created_at             TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_memory_snapshots_user_id    ON memory_snapshots (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_memory_snapshots_created_at ON memory_snapshots (created_at)")

    # ------------------------------------------------------------------
    # investor_profiles  (platform segment)
    # Previously created by migration 0014 but may be missing on some
    # installs due to the broken migration chain. IF NOT EXISTS is safe.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS investor_profiles (
            id                   SERIAL PRIMARY KEY,
            snapshot_date        DATE         NOT NULL,
            behavioral_patterns  TEXT,
            confirmed_biases     TEXT,
            top_lessons          TEXT,
            portfolio_bias       TEXT,
            active_thesis_count  INTEGER,
            win_rate_30d         FLOAT,
            avg_hold_days        FLOAT,
            summary_for_ai       TEXT,
            created_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_investor_profiles_snapshot_date ON investor_profiles (snapshot_date)")


def downgrade() -> None:
    # NOTE: downgrade intentionally does NOT drop these tables — they may
    # contain production data and were created outside Alembic originally.
    # To drop, run manually: DROP TABLE ai_interaction_logs, memory_snapshots, investor_profiles;
    pass
