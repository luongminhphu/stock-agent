"""add quantity to decision_logs

Revision ID: 20260529_0035
Revises: 20260528_0034
Create Date: 2026-05-29

Owner: thesis segment.
Adds optional quantity column to decision_logs to record number of shares traded.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260529_0035"
down_revision = "20260528_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "decision_logs",
        sa.Column("quantity", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("decision_logs", "quantity")
