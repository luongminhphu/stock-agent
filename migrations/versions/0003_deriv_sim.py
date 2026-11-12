"""Bảng giả lập HĐTL VN30 (paper trading): cấu hình, lệnh, tick P&L — hai sổ base/fade.

Revision ID: 0003_deriv_sim
Revises: 0002_drop_daily_agendas
Create Date: 2026-11-12

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_deriv_sim"
down_revision: str | None = "0002_drop_daily_agendas"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "deriv_sim_configs",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("series", sa.String(length=4), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("stop_points", sa.Float(), nullable=True),
        sa.Column("target_points", sa.Float(), nullable=True),
        sa.Column("alert_margin_pct", sa.Float(), nullable=True),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("stop_atr", sa.Float(), nullable=True),
        sa.Column("target_atr", sa.Float(), nullable=True),
        sa.Column("avoid_round", sa.Boolean(), nullable=False),
        sa.Column("crowd_atr", sa.Float(), nullable=True),
        sa.Column("reentry_blocked_on", sa.Date(), nullable=True),
        sa.Column("fade_reentry_blocked_on", sa.Date(), nullable=True),
        sa.Column("last_signal", sa.String(length=32), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "deriv_sim_ticks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("strategy", sa.String(length=8), nullable=False),
        sa.Column("contract_code", sa.String(length=16), nullable=True),
        sa.Column("side", sa.String(length=5), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("unrealized_pnl", sa.Float(), nullable=False),
        sa.Column("realized_total", sa.Float(), nullable=False),
        sa.Column("signal", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_deriv_sim_ticks_at"), "deriv_sim_ticks", ["at"], unique=False)
    op.create_index(
        op.f("ix_deriv_sim_ticks_user_id"), "deriv_sim_ticks", ["user_id"], unique=False
    )
    op.create_table(
        "deriv_sim_trades",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("strategy", sa.String(length=8), nullable=False),
        sa.Column("contract_code", sa.String(length=16), nullable=False),
        sa.Column("side", sa.String(length=5), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("stop_price", sa.Float(), nullable=True),
        sa.Column("target_price", sa.Float(), nullable=True),
        sa.Column("entry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_reason", sa.String(length=16), nullable=False),
        sa.Column("signal", sa.String(length=32), nullable=True),
        sa.Column("exit_price", sa.Float(), nullable=True),
        sa.Column("exit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_reason", sa.String(length=16), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=True),
        sa.Column("margin_alerted", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_deriv_sim_trades_user_id"), "deriv_sim_trades", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_deriv_sim_trades_user_id"), table_name="deriv_sim_trades")
    op.drop_table("deriv_sim_trades")
    op.drop_index(op.f("ix_deriv_sim_ticks_user_id"), table_name="deriv_sim_ticks")
    op.drop_index(op.f("ix_deriv_sim_ticks_at"), table_name="deriv_sim_ticks")
    op.drop_table("deriv_sim_ticks")
    op.drop_table("deriv_sim_configs")
