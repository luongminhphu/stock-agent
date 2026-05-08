"""Add brief_feedback table.

Revision ID: 20260508_0020
Revises: 20260507_0019
Create Date: 2026-05-08

Creates brief_feedback table to persist user outcome feedback for each
brief snapshot (acted / watching / skipped). Append-only design — one
row per user response.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260508_0020"
down_revision = "20260507_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "brief_feedback",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "brief_snapshot_id",
            sa.Integer(),
            sa.ForeignKey("brief_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_brief_feedback_user_id", "brief_feedback", ["user_id"])
    op.create_index("ix_brief_feedback_created_at", "brief_feedback", ["created_at"])
    op.create_index(
        "ix_brief_feedback_snapshot_user",
        "brief_feedback",
        ["brief_snapshot_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_brief_feedback_snapshot_user", table_name="brief_feedback")
    op.drop_index("ix_brief_feedback_created_at", table_name="brief_feedback")
    op.drop_index("ix_brief_feedback_user_id", table_name="brief_feedback")
    op.drop_table("brief_feedback")
