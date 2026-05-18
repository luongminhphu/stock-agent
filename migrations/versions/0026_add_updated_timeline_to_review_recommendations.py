"""add updated_timeline to review_recommendations

Revision ID: 0026
Revises: 0025
Create Date: 2026-05-18

Owner: thesis segment.
Adds updated_timeline column to review_recommendations table so
AI-suggested timeline updates for DELAYED catalysts are persisted
and can be applied to catalyst.expected_date via apply_recommendation().
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "review_recommendations",
        sa.Column("updated_timeline", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("review_recommendations", "updated_timeline")
