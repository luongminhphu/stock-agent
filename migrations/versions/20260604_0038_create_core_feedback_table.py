"""create core_feedback table

Revision ID: 20260604_0038
Revises: 20260604_0037
Create Date: 2026-06-04

Covers: CoreFeedback ORM model in src/core/models.py
(fix(core): persist FeedbackStore to DB + fix FeedbackEntry schema mismatch)

Owner: core segment.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260604_0038"
down_revision = "20260604_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "core_feedback",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("verdict_event_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("outcome", sa.String(length=32), nullable=False, server_default="not_acted"),
        sa.Column("trigger_source", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("user_note", sa.Text(), nullable=True),
        sa.Column("delta_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_core_feedback_verdict_event_id",
        "core_feedback",
        ["verdict_event_id"],
    )
    op.create_index(
        "ix_core_feedback_user_id",
        "core_feedback",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_core_feedback_user_id", table_name="core_feedback")
    op.drop_index("ix_core_feedback_verdict_event_id", table_name="core_feedback")
    op.drop_table("core_feedback")
