"""Add conviction_score to thesis_snapshots.

Revision ID: 20260516_0022
Revises: 20260508_0021
Create Date: 2026-05-16 00:22:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = "20260516_0022"
down_revision = "20260508_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "thesis_snapshots",
        sa.Column("conviction_score", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("thesis_snapshots", "conviction_score")
