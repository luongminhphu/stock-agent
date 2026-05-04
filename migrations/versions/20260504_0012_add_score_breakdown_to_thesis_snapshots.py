"""Add score_breakdown column to thesis_snapshots.

Revision ID: 20260504_0012
Revises: 20260504_0011
Create Date: 2026-05-04

Context
-------
review_service._persist_review() creates a ThesisSnapshot with
`score_breakdown=json.dumps(breakdown)` so the conviction timeline
has per-dimension score data. The ORM model was missing this column,
causing:

  TypeError: 'score_breakdown' is an invalid keyword argument for ThesisSnapshot
  → SQLAlchemy ROLLBACK → 503 Service Unavailable on POST /api/v1/thesis/:id/review

Note: migration 0004 (revision 0004_add_score_breakdown, separate chain) already
added this column to DBs that ran that branch. Using IF NOT EXISTS so this
migration is idempotent and safe for both cases.
"""
from __future__ import annotations

from alembic import op

revision: str = "20260504_0012"
down_revision: str | None = "20260504_0011"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Use raw SQL with IF NOT EXISTS — idempotent regardless of whether
    # migration 0004 (separate chain) already added this column.
    op.execute(
        "ALTER TABLE thesis_snapshots ADD COLUMN IF NOT EXISTS score_breakdown TEXT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE thesis_snapshots DROP COLUMN IF EXISTS score_breakdown"
    )
