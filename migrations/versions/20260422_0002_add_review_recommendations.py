"""Add review_recommendations table.

Revision ID: 0002_add_review_recommendations
Revises: 0001_initial_schema
Create Date: 2026-04-22

Context
-------
ReviewRecommendation holds per-item AI suggestions generated during a
ThesisReview.  Each row targets a single Assumption or Catalyst and moves
through PENDING → ACCEPTED | REJECTED | EXPIRED.

The table does NOT carry a direct thesis_id FK — thesis identity is
reachable via review_id → thesis_reviews.thesis_id, avoiding denormalisation.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002_add_review_recommendations"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "review_recommendations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # FK → thesis_reviews; thesis identity reachable via this join
        sa.Column("review_id", sa.Integer(), nullable=False),
        # "assumption" | "catalyst"
        sa.Column(
            "target_type",
            sa.Enum(
                "assumption",
                "catalyst",
                name="recommendationtargettype",
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column(
            "target_description",
            sa.Text(),
            nullable=False,
            comment="Snapshot mô tả tại thời điểm review, để hiển thị cho user",
        ),
        # "valid" | "invalid" | "uncertain" | "triggered" | "expired" | "cancelled"
        sa.Column("recommended_status", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "accepted",
                "rejected",
                "expired",
                name="recommendationstatus",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "acted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Khi user ACCEPTED hoặc REJECTED",
        ),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["thesis_reviews.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Lookup: all recommendations for a review (most common query)
    op.create_index(
        "ix_review_recommendations_review_id",
        "review_recommendations",
        ["review_id"],
    )
    # Lookup: all PENDING recommendations (used by /recommendations command)
    op.create_index(
        "ix_review_recommendations_status",
        "review_recommendations",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_recommendations_status", table_name="review_recommendations")
    op.drop_index("ix_review_recommendations_review_id", table_name="review_recommendations")
    op.drop_table("review_recommendations")
    op.execute("DROP TYPE IF EXISTS recommendationstatus")
    op.execute("DROP TYPE IF EXISTS recommendationtargettype")
