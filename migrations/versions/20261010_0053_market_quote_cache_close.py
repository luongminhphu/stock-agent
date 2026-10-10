"""market_quote_cache: thêm close — giá đóng cửa chính thức (Wave C1)

Revision ID: 20261010_0053
Revises: 20260920_0052
Create Date: 2026-10-10

Trước đây "giá đóng cửa" ngoài giờ = last_known của lần fetch cuối trong phiên
(thường 14:15–14:30, trước ATC) → EOD snapshot, outcome filler và dashboard dùng
sai giá. QuoteService.capture_eod_close (14:50 ICT) ghi close = giá khớp ATC vào
cột này; warm-load sau restart khôi phục Quote.close.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261010_0053"
down_revision = "20260920_0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "market_quote_cache",
        sa.Column(
            "close",
            sa.Float(),
            nullable=True,
            comment="Wave C1: giá đóng cửa chính thức (ATC) do capture_eod_close ghi; NULL trong phiên",
        ),
    )


def downgrade() -> None:
    op.drop_column("market_quote_cache", "close")
