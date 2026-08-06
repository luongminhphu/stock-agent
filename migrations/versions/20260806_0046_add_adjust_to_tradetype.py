"""add 'adjust' value to tradetype enum

Revision ID: 20260806_0046
Revises: 20260806_0045
Create Date: 2026-08-06

PortfolioService.apply_stock_split() ghi Trade(ADJUST) làm audit trail cho
stock dividend / split. PG enum tradetype chỉ có ('buy','sell') — INSERT sẽ
fail nếu không thêm value.

Postgres: ALTER TYPE ... ADD VALUE IF NOT EXISTS (PG 12+). Lưu ý: enum value
mới không dùng được trong cùng transaction với câu ADD VALUE trên PG < 12;
production đang PG hiện đại nên an toàn.
SQLite: enum là VARCHAR — không cần DDL.
"""

from __future__ import annotations

from alembic import op

revision = "20260806_0046"
down_revision = "20260806_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE tradetype ADD VALUE IF NOT EXISTS 'adjust'")


def downgrade() -> None:
    # PG không hỗ trợ DROP enum value — downgrade là no-op trên PG.
    # Để thật sự rollback phải recreate type; không đáng rủi ro cho value
    # đã có data. SQLite không cần làm gì.
    pass
