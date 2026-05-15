"""Add conviction_score to thesis_snapshots.

Revision ID: 20260516_0022
Revises: 20260508_0021_add_ai_memory_tables
Create Date: 2026-05-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260516_0022"
down_revision: str = "20260508_0021_add_ai_memory_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "thesis_snapshots",
        sa.Column("conviction_score", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("thesis_snapshots", "conviction_score")
