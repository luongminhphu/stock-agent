"""add structured fields to review_recommendations

Revision ID: 20260504_0013
Revises: 20260504_0012
Create Date: 2026-05-04

Problem: ReviewRecommendation ORM was missing target_type, target_id,
target_description, recommended_status, reason, acted_at — causing
'invalid keyword argument' crash in _auto_apply_recommendations.

All new columns are nullable for zero-downtime deploy (no backfill needed).
"""

from alembic import op
import sqlalchemy as sa

revision = "20260504_0013"
down_revision = "20260504_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "review_recommendations",
        sa.Column("target_type", sa.String(32), nullable=True),
    )
    op.add_column(
        "review_recommendations",
        sa.Column("target_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "review_recommendations",
        sa.Column("target_description", sa.Text(), nullable=True),
    )
    op.add_column(
        "review_recommendations",
        sa.Column("recommended_status", sa.String(32), nullable=True),
    )
    op.add_column(
        "review_recommendations",
        sa.Column("reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "review_recommendations",
        sa.Column("acted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("review_recommendations", "acted_at")
    op.drop_column("review_recommendations", "reason")
    op.drop_column("review_recommendations", "recommended_status")
    op.drop_column("review_recommendations", "target_description")
    op.drop_column("review_recommendations", "target_id")
    op.drop_column("review_recommendations", "target_type")
