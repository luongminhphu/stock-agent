"""add needs_monitoring to assumptionsstatus enum

Revision ID: 20260516_0023
Revises: 20260516_0022
Create Date: 2026-05-16
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260516_0023"
down_revision = "20260516_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD VALUE is additive and safe on PostgreSQL — no table lock required.
    op.execute("ALTER TYPE assumptionsstatus ADD VALUE IF NOT EXISTS 'needs_monitoring'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values natively.
    # Downgrade is a no-op; 'needs_monitoring' rows must be migrated manually
    # before dropping the value via a custom script if ever needed.
    pass
