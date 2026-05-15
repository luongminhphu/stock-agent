"""Merge watchlist_scans/brief_snapshots and review_recommendations heads.

Revision ID: 0003_merge_heads
Revises: 0002_add_review_recommendations
Create Date: 2026-04-22

No schema changes — this is a merge-only revision that resolves the
multiple-head situation caused by two branches both descending from
0001_initial_schema.

Fix (2026-05-16): removed ghost revision 96b70988d3a9 from down_revision.
That revision file never existed in the repo and caused Alembic to crash
with KeyError at _revision_map build time. Chain is now linear.
"""
from __future__ import annotations

from alembic import op

revision: str = "0003_merge_heads"
down_revision: str = "0002_add_review_recommendations"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
