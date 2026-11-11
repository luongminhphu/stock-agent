"""derivative_positions — sổ vị thế HĐTL VN30

Revision ID: 20261111_0057
Revises: 20261111_0056
Create Date: 2026-11-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261111_0057"
down_revision = "20261111_0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "derivative_positions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("contract_code", sa.String(length=16), nullable=False),
        sa.Column("side", sa.String(length=5), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_price", sa.Float(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=True),
        sa.Column("purpose", sa.String(length=16), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index("ix_derivative_positions_user_id", "derivative_positions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_derivative_positions_user_id", table_name="derivative_positions")
    op.drop_table("derivative_positions")
