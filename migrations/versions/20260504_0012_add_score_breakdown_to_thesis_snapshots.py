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

Fix: add nullable TEXT column. Existing rows get NULL (no data loss).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260504_0012"
down_revision: str | None = "20260504_0011"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "thesis_snapshots",
        sa.Column("score_breakdown", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("thesis_snapshots", "score_breakdown")
