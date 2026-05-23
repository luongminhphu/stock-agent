"""create evolution_log table

Revision ID: 0013_evolution_log
Revises: 0012_engine_feedback
Create Date: 2026-05-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_evolution_log"
down_revision = "0012_engine_feedback"  # set to actual previous revision if different
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evolution_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("target", sa.String(32), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("evidence_summary", sa.Text, nullable=False, server_default=""),
        sa.Column("proposed_change", sa.Text, nullable=False),
        sa.Column("risk_level", sa.String(8), nullable=False, server_default="low"),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_evolution_log_run_id",    "evolution_log", ["run_id"])
    op.create_index("ix_evolution_log_status",    "evolution_log", ["status"])
    op.create_index("ix_evolution_log_created_at", "evolution_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_evolution_log_created_at", table_name="evolution_log")
    op.drop_index("ix_evolution_log_status",     table_name="evolution_log")
    op.drop_index("ix_evolution_log_run_id",     table_name="evolution_log")
    op.drop_table("evolution_log")
