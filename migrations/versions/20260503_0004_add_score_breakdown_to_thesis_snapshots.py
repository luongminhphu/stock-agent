"""Add score_breakdown column to thesis_snapshots.

Revision ID: 0004_add_score_breakdown
Revises: 0003_merge_heads
Create Date: 2026-05-03

Adds TEXT column score_breakdown to thesis_snapshots.
Nullable — existing rows (legacy snapshots) will have NULL.
JSON dict: {"assumption_health": float, "catalyst_progress": float,
             "risk_reward": float, "review_confidence": float}
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004_add_score_breakdown"
down_revision: str = "0003_merge_heads"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "thesis_snapshots",
        sa.Column(
            "score_breakdown",
            sa.Text(),
            nullable=True,
            comment="JSON breakdown từ ScoringService.compute_with_breakdown(), nullable cho legacy rows",
        ),
    )


def downgrade() -> None:
    op.drop_column("thesis_snapshots", "score_breakdown")
