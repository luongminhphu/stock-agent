"""market_daily_candles + market_candle_coverage — lưu lịch sử nến ngày

Revision ID: 20261111_0055
Revises: 20261010_0054
Create Date: 2026-11-11

Base-rate cần ~10 năm nến ngày cho mỗi mã. Lưu vào DB để mỗi ngày chỉ tải phần còn
thiếu từ API của CTCK thay vì tải lại toàn bộ (giảm tải, tránh bị chặn).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261111_0055"
down_revision = "20261010_0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_daily_candles",
        sa.Column("ticker", sa.String(length=20), primary_key=True),
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_table(
        "market_candle_coverage",
        sa.Column("ticker", sa.String(length=20), primary_key=True),
        sa.Column("first_date", sa.Date(), nullable=False),
        sa.Column("last_date", sa.Date(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("market_candle_coverage")
    op.drop_table("market_daily_candles")
