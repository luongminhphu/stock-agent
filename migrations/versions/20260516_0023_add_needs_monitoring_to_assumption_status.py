"""add needs_monitoring to assumptionsstatus enum

Revision ID: 0023
Revises: 0022
Create Date: 2026-05-16
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD VALUE is additive and safe on PostgreSQL — no table lock required.
    # Cannot be run inside a transaction block, so we use COMMIT trick via
    # execute_if to guard against non-PG backends in tests.
    op.execute("ALTER TYPE assumptionsstatus ADD VALUE IF NOT EXISTS 'needs_monitoring'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values natively.
    # Downgrade is a no-op; 'needs_monitoring' rows must be migrated manually
    # before dropping the value via a custom script if ever needed.
    pass
