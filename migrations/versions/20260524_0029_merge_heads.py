"""merge heads — main chain only

Revision ID: 20260524_0029
Revises: 20260524_0028
Create Date: 2026-05-24

Merge-only migration — no schema changes.
0012/0013 branch removed (orphan, down_revision=None).
"""

from alembic import op

revision = "20260524_0029"
down_revision = "20260524_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
