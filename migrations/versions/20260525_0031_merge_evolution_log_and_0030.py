"""Merge 0013_evolution_log and 20260524_0030 heads.

Revision ID: 20260525_0031
Revises: 0013_evolution_log, 20260524_0030
Create Date: 2026-05-25

No schema changes — merge-only revision that resolves the two divergent
heads so `alembic upgrade head` works again.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260525_0031"
down_revision = ("0013_evolution_log", "20260524_0030")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
