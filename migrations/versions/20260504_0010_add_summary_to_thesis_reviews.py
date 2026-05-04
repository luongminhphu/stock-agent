"""Add summary column to thesis_reviews.

Revision ID: 20260504_0010
Revises: 20260504_0009
Create Date: 2026-05-04

Column `summary` was added to the ThesisReview ORM model but the
corresponding migration was never created, causing:

    asyncpg.exceptions.UndefinedColumnError:
        column thesis_reviews.summary does not exist

This affected all endpoints that eager-load ThesisReview relationships
(GET /thesis/{id}/reviews, /assumptions, /catalysts).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260504_0010"
down_revision: str = "20260504_0009"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "thesis_reviews",
        sa.Column("summary", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("thesis_reviews", "summary")
