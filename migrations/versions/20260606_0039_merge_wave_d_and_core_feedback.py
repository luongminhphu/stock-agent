"""merge Wave D branch with core_feedback branch

Revision ID: 20260606_0039
Revises: 20260606_0038, 20260604_0038
Create Date: 2026-06-06

No schema changes — merge-only revision that resolves two divergent heads:

  Branch A (main):  20260604_0037 → 20260604_0038 (create_core_feedback_table)
  Branch B (Wave D): 20260606_0037 → 20260606_0038 (wave_d2_synthesis_cooldown)

Both branches diverged from 20260604_0036. This merge revision makes
alembic upgrade head safe to run on any DB state where either or both
branches have been applied.
"""

from alembic import op

# revision identifiers
revision = "20260606_0039"
down_revision = ("20260606_0038", "20260604_0038")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass  # merge only


def downgrade() -> None:
    pass  # merge only
