"""merge heads: 0013_evolution_log + 20260524_0028

Revision ID: 20260524_0029
Revises: 0013_evolution_log, 20260524_0028
Create Date: 2026-05-24

Merge-only migration — no schema changes.
Gathers the legacy 0012/0013 branch and the main migration chain
into a single head so `alembic upgrade head` works cleanly.
"""

from alembic import op

revision = "20260524_0029"
down_revision = ("0013_evolution_log", "20260524_0028")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
