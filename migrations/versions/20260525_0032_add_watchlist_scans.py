"""Add watchlist_scans table.

Revision ID: 20260525_0032
Revises: 20260525_0031
Create Date: 2026-05-25

watchlist_scans stores per-user scan snapshots produced by ScanService.
Owner: watchlist segment.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260525_0032"
down_revision = "20260525_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watchlist_scans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_watchlist_scans_user_id", "watchlist_scans", ["user_id"])
    op.create_index("ix_watchlist_scans_scanned_at", "watchlist_scans", ["scanned_at"])


def downgrade() -> None:
    op.drop_index("ix_watchlist_scans_scanned_at", table_name="watchlist_scans")
    op.drop_index("ix_watchlist_scans_user_id", table_name="watchlist_scans")
    op.drop_table("watchlist_scans")
