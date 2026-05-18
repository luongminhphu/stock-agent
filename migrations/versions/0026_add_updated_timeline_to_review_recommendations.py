"""add updated_timeline to review_recommendations

Revision ID: 0026
Revises: 20260516_0023
Create Date: 2026-05-18
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0026"
down_revision = "20260516_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "review_recommendations",
        sa.Column("updated_timeline", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("review_recommendations", "updated_timeline")
