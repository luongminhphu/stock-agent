"""Add composite index on user_behavior_logs(user_id, signal).

Revision ID: 20260604_0036
Revises: 20260529_0035
Create Date: 2026-06-04

Owner: ai segment.

Rationale:
    MemoryConsolidator.synthesize_patterns() queries user_behavior_logs
    filtered by both user_id AND signal (e.g. "sold", "ignored") over a
    rolling time window. Without a composite index, every synthesis call
    does a full table scan as the log grows.

    The user_id column already has a single-column index (from initial
    ai_memory tables migration). This migration adds the composite variant
    which is strictly more efficient for the (user_id, signal) filter pattern
    and does not conflict with the existing single-column index.

No data changes. Fully reversible.
"""

from __future__ import annotations

from alembic import op

revision = "20260604_0036"
down_revision = "20260529_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_user_behavior_logs_user_id_signal",
        "user_behavior_logs",
        ["user_id", "signal"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_behavior_logs_user_id_signal",
        table_name="user_behavior_logs",
    )
