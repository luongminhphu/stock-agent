"""Merge watchlist_scans/brief_snapshots and review_recommendations heads.

Revision ID: 0003_merge_heads
Revises: 96b70988d3a9, 0002_add_review_recommendations
Create Date: 2026-04-22

No schema changes — this is a merge-only revision that resolves the
multiple-head situation caused by two branches both descending from
0001_initial_schema.
"""
from __future__ import annotations

from alembic import op

revision: str = "0003_merge_heads"
down_revision: tuple[str, str] = ("96b70988d3a9", "0002_add_review_recommendations")
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
