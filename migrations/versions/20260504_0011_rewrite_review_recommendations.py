"""Rewrite review_recommendations schema to match current ORM model.

Revision ID: 20260504_0011
Revises: 20260504_0010
Create Date: 2026-05-04

Context
-------
Migration 0002 created review_recommendations with a per-assumption/catalyst
structure:
  - target_type (enum: assumption | catalyst)
  - target_id   (FK to assumption or catalyst row)
  - target_description (snapshot text)
  - recommended_status (string)
  - reason      (TEXT — the actual AI reasoning)
  - acted_at    (DateTime — when user accepted/rejected)

The ORM model (src/thesis/models.py) has since been rewritten to a simpler
free-text structure with a single `content` TEXT column, matching the
ReviewRecommendation dataclass used by the AI layer.

This mismatch causes:
  UndefinedColumnError: column review_recommendations.content does not exist
  → 500 Internal Server Error on every GET /api/v1/thesis/:id request.

Data migration strategy:
  - content ← reason  (carries semantic meaning forward)
  - All other dropped columns have no active API/UI consumers.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260504_0011"
down_revision: str | None = "20260504_0010"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 1. Add `content` column with a temporary default so existing rows pass NOT NULL
    op.add_column(
        "review_recommendations",
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
    )

    # 2. Populate content from the existing `reason` column (best semantic match)
    op.execute(
        "UPDATE review_recommendations SET content = reason WHERE content = ''"
    )

    # 3. Remove the temporary server default — new rows must supply content explicitly
    op.alter_column("review_recommendations", "content", server_default=None)

    # 4. Drop obsolete columns (order matters: drop enum-typed column first)
    op.drop_column("review_recommendations", "target_type")
    op.drop_column("review_recommendations", "target_id")
    op.drop_column("review_recommendations", "target_description")
    op.drop_column("review_recommendations", "recommended_status")
    op.drop_column("review_recommendations", "reason")
    op.drop_column("review_recommendations", "acted_at")

    # 5. Drop orphaned enum type no longer referenced by any column
    op.execute("DROP TYPE IF EXISTS recommendationtargettype")


def downgrade() -> None:
    # Re-add dropped columns
    op.add_column(
        "review_recommendations",
        sa.Column("acted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "review_recommendations",
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
    )
    op.execute(
        "UPDATE review_recommendations SET reason = content WHERE reason = ''"
    )
    op.alter_column("review_recommendations", "reason", server_default=None)

    op.add_column(
        "review_recommendations",
        sa.Column("recommended_status", sa.String(32), nullable=False, server_default="valid"),
    )
    op.alter_column("review_recommendations", "recommended_status", server_default=None)

    op.add_column(
        "review_recommendations",
        sa.Column("target_description", sa.Text(), nullable=False, server_default=""),
    )
    op.alter_column("review_recommendations", "target_description", server_default=None)

    op.add_column(
        "review_recommendations",
        sa.Column("target_id", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("review_recommendations", "target_id", server_default=None)

    # Re-create the enum type before adding the enum column
    op.execute(
        "CREATE TYPE recommendationtargettype AS ENUM ('assumption', 'catalyst')"
    )
    op.add_column(
        "review_recommendations",
        sa.Column(
            "target_type",
            sa.Enum("assumption", "catalyst", name="recommendationtargettype"),
            nullable=False,
            server_default="assumption",
        ),
    )
    op.alter_column("review_recommendations", "target_type", server_default=None)

    # Drop the content column added in upgrade
    op.drop_column("review_recommendations", "content")
