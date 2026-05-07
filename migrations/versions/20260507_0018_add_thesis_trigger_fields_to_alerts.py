"""add thesis trigger fields to alerts

Revision ID: 20260507_0018
Revises: 20260506_0017
Create Date: 2026-05-07

Adds columns required by AlertService.create_thesis_trigger_rule() /
rule_exists_by_dedup_key() that were missing from the alerts table,
causing AttributeError when StressTestSubscriber handled
StressTestCompletedEvent.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260507_0018"
down_revision = "20260506_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Add 'thesis_trigger' to the alertconditiontype enum
    # ------------------------------------------------------------------
    # PostgreSQL requires ALTER TYPE … ADD VALUE outside a transaction
    # for enum changes.  Alembic handles this via execute_if or raw SQL.
    op.execute(
        "ALTER TYPE alertconditiontype ADD VALUE IF NOT EXISTS 'thesis_trigger'"
    )

    # ------------------------------------------------------------------
    # 2. Add new nullable columns to alerts
    # ------------------------------------------------------------------
    with op.batch_alter_table("alerts") as batch_op:
        batch_op.add_column(
            sa.Column("label", sa.String(256), nullable=True)
        )
        batch_op.add_column(
            sa.Column("thesis_id", sa.String(64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("dedup_key", sa.String(128), nullable=True)
        )
        batch_op.add_column(
            sa.Column("source_event_id", sa.String(64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("priority", sa.String(16), nullable=True)
        )

    # ------------------------------------------------------------------
    # 3. Create indexes on thesis_id and dedup_key for fast dedup lookups
    # ------------------------------------------------------------------
    op.create_index(
        "ix_alerts_thesis_id",
        "alerts",
        ["thesis_id"],
        unique=False,
        postgresql_where=sa.text("thesis_id IS NOT NULL"),
    )
    op.create_index(
        "ix_alerts_dedup_key",
        "alerts",
        ["dedup_key"],
        unique=False,
        postgresql_where=sa.text("dedup_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_alerts_dedup_key", table_name="alerts")
    op.drop_index("ix_alerts_thesis_id", table_name="alerts")

    with op.batch_alter_table("alerts") as batch_op:
        batch_op.drop_column("priority")
        batch_op.drop_column("source_event_id")
        batch_op.drop_column("dedup_key")
        batch_op.drop_column("thesis_id")
        batch_op.drop_column("label")

    # Note: PostgreSQL does not support removing enum values.
    # 'thesis_trigger' must remain in the alertconditiontype enum on downgrade.
