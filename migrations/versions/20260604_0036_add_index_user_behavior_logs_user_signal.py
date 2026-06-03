"""Create user_behavior_logs table and add composite index (user_id, signal).

Revision ID: 20260604_0036
Revises: 20260529_0035
Create Date: 2026-06-04

Owner: ai segment.

Rationale:
    UserBehaviorLog model (src/ai/memory/user_behavior_log.py) existed
    without a corresponding DDL migration. This migration creates the
    table from scratch and adds the composite index needed by
    MemoryConsolidator.synthesize_patterns() which filters by both
    user_id AND signal over a rolling time window.

Single-column indexes mirror the ORM-level index=True declarations:
    ix_user_behavior_logs_user_id, ix_user_behavior_logs_interaction_log_id,
    ix_user_behavior_logs_ticker, ix_user_behavior_logs_created_at

Composite index:
    ix_user_behavior_logs_user_id_signal — avoids full table scan on the
    primary pattern synthesis query pattern.

No data loss. Fully reversible.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260604_0036"
down_revision = "20260529_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_behavior_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column(
            "signal",
            sa.String(32),
            nullable=False,
            comment="bought | sold | watched | ignored | flagged",
        ),
        sa.Column(
            "source",
            sa.String(32),
            nullable=False,
            server_default="discord_reaction",
            comment="discord_reaction | command | api | feedback_listener",
        ),
        sa.Column(
            "interaction_log_id",
            sa.Integer(),
            nullable=True,
            comment="FK to AIInteractionLog.id — null if signal has no AI source",
        ),
        sa.Column("ticker", sa.String(16), nullable=True),
        sa.Column("agent_type", sa.String(64), nullable=True),
        sa.Column("note", sa.String(512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_behavior_logs"),
    )

    # Single-column indexes (mirror ORM index=True declarations)
    op.create_index(
        "ix_user_behavior_logs_user_id",
        "user_behavior_logs",
        ["user_id"],
    )
    op.create_index(
        "ix_user_behavior_logs_interaction_log_id",
        "user_behavior_logs",
        ["interaction_log_id"],
    )
    op.create_index(
        "ix_user_behavior_logs_ticker",
        "user_behavior_logs",
        ["ticker"],
    )
    op.create_index(
        "ix_user_behavior_logs_created_at",
        "user_behavior_logs",
        ["created_at"],
    )

    # Composite index for pattern synthesis: WHERE user_id = ? AND signal = ?
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
    op.drop_index(
        "ix_user_behavior_logs_created_at",
        table_name="user_behavior_logs",
    )
    op.drop_index(
        "ix_user_behavior_logs_ticker",
        table_name="user_behavior_logs",
    )
    op.drop_index(
        "ix_user_behavior_logs_interaction_log_id",
        table_name="user_behavior_logs",
    )
    op.drop_index(
        "ix_user_behavior_logs_user_id",
        table_name="user_behavior_logs",
    )
    op.drop_table("user_behavior_logs")
