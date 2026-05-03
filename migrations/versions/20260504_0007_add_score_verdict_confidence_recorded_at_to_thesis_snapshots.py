"""add score, verdict, confidence, recorded_at to thesis_snapshots

Revision ID: 20260504_0007
Revises: 20260504_0006
Create Date: 2026-05-04

Adds 4 new nullable columns to thesis_snapshots to support review-triggered
snapshots created by review_service. Legacy market-snapshot rows are unaffected
(all columns default to NULL).
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260504_0007"
down_revision = "20260504_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "thesis_snapshots",
        sa.Column("score", sa.Float(), nullable=True, comment="Conviction score tại thời điểm review (0-100)"),
    )
    op.add_column(
        "thesis_snapshots",
        sa.Column("verdict", sa.String(32), nullable=True, comment="ReviewVerdict value tại thời điểm review"),
    )
    op.add_column(
        "thesis_snapshots",
        sa.Column("confidence", sa.Float(), nullable=True, comment="AI confidence tại thời điểm review (0-1)"),
    )
    op.add_column(
        "thesis_snapshots",
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True, comment="Timestamp của review tạo ra snapshot này"),
    )
    # Make price_at_snapshot nullable (was NOT NULL in initial schema,
    # but review-triggered snapshots don't have a price)
    op.alter_column("thesis_snapshots", "price_at_snapshot", nullable=True)


def downgrade() -> None:
    op.alter_column("thesis_snapshots", "price_at_snapshot", nullable=False)
    op.drop_column("thesis_snapshots", "recorded_at")
    op.drop_column("thesis_snapshots", "confidence")
    op.drop_column("thesis_snapshots", "verdict")
    op.drop_column("thesis_snapshots", "score")
