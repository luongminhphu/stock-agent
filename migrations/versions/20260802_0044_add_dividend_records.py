"""Create dividend_records table + dividendtype enum.

Revision ID: 20260802_0044
Revises: 20260802_0043
Create Date: 2026-08-02

Context
-------
DividendRecord model (src/portfolio/models.py) has existed since the
portfolio segment was added, but no migration ever created the table.
The gap stayed invisible until Wave 5a (sizing-preview API): when
PORTFOLIO_CASH_VND is unset, PositionSizingService falls back to
estimating cash from realized PnL + dividends, and the dividend query
hit a missing relation on production PostgreSQL:

    relation "dividend_records" does not exist

The service already catches this and degrades gracefully (cash estimate
excludes dividends, sizing stays conservative), so this migration is a
correctness fix, not an incident hotfix.

Creates:
  - dividendtype enum (cash | stock)   — PostgreSQL only
  - dividend_records table             — all dialects
  - indexes on user_id, ticker, position_id (matches model `index=True`)
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0044"
down_revision: str = "20260802_0043"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        # Enum có thể đã tồn tại trên production (được tạo ngoài Alembic —
        # ví dụ một lần bootstrap/auto-create trước đây). Dùng raw SQL để
        # kiểm soát hoàn toàn: không tạo nếu đã có, và bảng dùng raw SQL
        # tham chiếu enum type theo tên thay vì để SQLAlchemy emit
        # CREATE TYPE lần nữa trong before_create.
        op.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'dividendtype') THEN
                    CREATE TYPE dividendtype AS ENUM ('cash', 'stock');
                END IF;
            END
            $$;
            """
        )
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS dividend_records (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL,
                ticker VARCHAR(10) NOT NULL,
                position_id INTEGER REFERENCES positions(id) ON DELETE SET NULL,
                qty DOUBLE PRECISION NOT NULL,
                dividend_per_share DOUBLE PRECISION NOT NULL,
                total_amount DOUBLE PRECISION NOT NULL,
                dividend_type dividendtype NOT NULL DEFAULT 'cash',
                ex_date TIMESTAMPTZ,
                paid_at TIMESTAMPTZ NOT NULL,
                note TEXT
            );
            """
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_dividend_records_user_id "
            "ON dividend_records (user_id)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_dividend_records_ticker "
            "ON dividend_records (ticker)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_dividend_records_position_id "
            "ON dividend_records (position_id)"
        )
        return

    # SQLite / other dialects — no native enum, plain VARCHAR.
    op.create_table(
        "dividend_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column(
            "position_id",
            sa.Integer(),
            sa.ForeignKey("positions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("dividend_per_share", sa.Float(), nullable=False),
        sa.Column("total_amount", sa.Float(), nullable=False),
        sa.Column(
            "dividend_type",
            sa.String(length=16),
            nullable=False,
            server_default="cash",
        ),
        sa.Column("ex_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index("ix_dividend_records_user_id", "dividend_records", ["user_id"])
    op.create_index("ix_dividend_records_ticker", "dividend_records", ["ticker"])
    op.create_index(
        "ix_dividend_records_position_id", "dividend_records", ["position_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_dividend_records_position_id", table_name="dividend_records")
    op.drop_index("ix_dividend_records_ticker", table_name="dividend_records")
    op.drop_index("ix_dividend_records_user_id", table_name="dividend_records")
    op.drop_table("dividend_records")
    if op.get_bind().dialect.name == "postgresql":
        sa.Enum(name="dividendtype").drop(op.get_bind(), checkfirst=True)
