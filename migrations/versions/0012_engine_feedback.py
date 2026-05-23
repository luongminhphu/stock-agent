"""create engine_feedback table

Revision ID: 0012_engine_feedback
Revises: (auto — set to previous revision ID in your chain)
Create Date: 2026-05-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_engine_feedback"
down_revision = None   # TODO: set to your latest migration revision ID
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "engine_feedback",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("verdict_event_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("verdict", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("trigger_source", sa.String(64), nullable=False, server_default=""),
        sa.Column("user_note", sa.Text, nullable=True),
        sa.Column("delta_score", sa.Float, nullable=False, server_default="0"),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_engine_feedback_verdict_event_id",
        "engine_feedback",
        ["verdict_event_id"],
    )
    op.create_index(
        "ix_engine_feedback_user_id",
        "engine_feedback",
        ["user_id"],
    )
    op.create_index(
        "ix_engine_feedback_submitted_at",
        "engine_feedback",
        ["submitted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_engine_feedback_submitted_at", table_name="engine_feedback")
    op.drop_index("ix_engine_feedback_user_id", table_name="engine_feedback")
    op.drop_index("ix_engine_feedback_verdict_event_id", table_name="engine_feedback")
    op.drop_table("engine_feedback")
