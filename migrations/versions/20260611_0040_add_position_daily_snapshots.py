"""Add position_daily_snapshots table — EOD persistent P&L per position.

Revision ID: 20260611_0040
Revises: 20260606_0039
Create Date: 2026-06-11

Problem:
    Portfolio dashboard P&L was computed on-the-fly from QuoteService every request.
    Outside trading hours (no live prices) positions showed P&L = 0 or were missing.
    After server restart, all in-memory price cache was lost.

Solution:
    Persist end-of-day P&L snapshot per (user_id, ticker, snapshot_date).
    EodSnapshotService writes at 15:20 ICT every trading day.
    Dashboard reads from snapshot (primary source) + overlays realtime price
    only when market is open.

Schema:
    position_daily_snapshots (
        id               SERIAL PK,
        user_id          VARCHAR(64) NOT NULL,
        ticker           VARCHAR(10) NOT NULL,
        snapshot_date    DATE NOT NULL,
        qty              FLOAT NOT NULL,
        avg_cost         FLOAT NOT NULL,
        close_price      FLOAT NOT NULL,     -- giá đóng cửa thực tế
        cost_basis       FLOAT NOT NULL,
        market_value     FLOAT NOT NULL,
        unrealized_pnl   FLOAT NOT NULL,
        unrealized_pct   FLOAT NOT NULL,
        thesis_id        INTEGER NULL,
        created_at       TIMESTAMPTZ NOT NULL,
        UNIQUE(user_id, ticker, snapshot_date)
    )
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260611_0040"
down_revision = "20260606_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "position_daily_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("snapshot_date", sa.Date, nullable=False),
        sa.Column("qty", sa.Float, nullable=False),
        sa.Column("avg_cost", sa.Float, nullable=False),
        sa.Column("close_price", sa.Float, nullable=False),
        sa.Column("cost_basis", sa.Float, nullable=False),
        sa.Column("market_value", sa.Float, nullable=False),
        sa.Column("unrealized_pnl", sa.Float, nullable=False),
        sa.Column("unrealized_pct", sa.Float, nullable=False),
        sa.Column("thesis_id", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id", "ticker", "snapshot_date",
            name="uq_position_daily_snapshot",
        ),
    )
    op.create_index("ix_position_daily_snapshots_user_id", "position_daily_snapshots", ["user_id"])
    op.create_index("ix_position_daily_snapshots_ticker", "position_daily_snapshots", ["ticker"])
    op.create_index("ix_position_daily_snapshots_snapshot_date", "position_daily_snapshots", ["snapshot_date"])


def downgrade() -> None:
    op.drop_index("ix_position_daily_snapshots_snapshot_date", "position_daily_snapshots")
    op.drop_index("ix_position_daily_snapshots_ticker", "position_daily_snapshots")
    op.drop_index("ix_position_daily_snapshots_user_id", "position_daily_snapshots")
    op.drop_table("position_daily_snapshots")
