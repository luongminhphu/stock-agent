"""add portfolio tables

Revision ID: 20260503_0005
Revises: 20260503_0004
Create Date: 2026-05-03

Adds two tables for the portfolio segment:
  positions  — current state of each holding (qty, avg_cost, open/closed)
  trades     — immutable record of every BUY/SELL execution

Relationship: positions 1 → N trades (CASCADE DELETE).

Design notes:
  - avg_cost is stored on positions (recalculated on buy by PortfolioService).
  - realized_pnl on trades is SELL-only: (price - avg_cost) * qty.
  - realized_pnl on positions is running total of all SELL trades for that position.
  - thesis_id is an optional FK-by-convention (not enforced by DB constraint)
    to avoid hard coupling between portfolio and thesis segments.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260503_0005"
down_revision: str = "20260503_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # positions
    # ------------------------------------------------------------------
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("avg_cost", sa.Float(), nullable=False),
        sa.Column("thesis_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=False, server_default="0.0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_positions_user_id", "positions", ["user_id"])
    op.create_index("ix_positions_thesis_id", "positions", ["thesis_id"])

    # ------------------------------------------------------------------
    # trades
    # ------------------------------------------------------------------
    op.create_table(
        "trades",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column("position_id", sa.Integer(), nullable=False),
        sa.Column(
            "trade_type",
            sa.Enum("buy", "sell", name="tradetype", create_constraint=False),
            nullable=False,
        ),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("realized_pnl", sa.Float(), nullable=True),
        sa.Column("traded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["position_id"],
            ["positions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trades_user_id", "trades", ["user_id"])
    op.create_index("ix_trades_position_id", "trades", ["position_id"])


def downgrade() -> None:
    op.drop_index("ix_trades_position_id", table_name="trades")
    op.drop_index("ix_trades_user_id", table_name="trades")
    op.drop_table("trades")

    op.drop_index("ix_positions_thesis_id", table_name="positions")
    op.drop_index("ix_positions_user_id", table_name="positions")
    op.drop_table("positions")
