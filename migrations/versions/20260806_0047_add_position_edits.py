"""add position_edits table

Revision ID: 20260806_0047
Revises: 20260806_0046
Create Date: 2026-08-06

Audit trail cho edit_position (nút ✎ trên dashboard): lưu old/new qty +
avg_cost mỗi lần sửa tay. PG path dùng raw SQL idempotent (pattern 0044);
SQLite path dùng op.create_table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260806_0047"
down_revision = "20260806_0046"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgres():
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS position_edits (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL,
                ticker VARCHAR(10) NOT NULL,
                position_id INTEGER NOT NULL REFERENCES positions(id),
                old_qty DOUBLE PRECISION NOT NULL,
                new_qty DOUBLE PRECISION NOT NULL,
                old_avg_cost DOUBLE PRECISION NOT NULL,
                new_avg_cost DOUBLE PRECISION NOT NULL,
                edited_at TIMESTAMP NOT NULL DEFAULT now()
            )
            """
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_position_edits_user_id ON position_edits (user_id)"
        )
    else:
        op.create_table(
            "position_edits",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("ticker", sa.String(10), nullable=False),
            sa.Column(
                "position_id",
                sa.Integer,
                sa.ForeignKey("positions.id"),
                nullable=False,
            ),
            sa.Column("old_qty", sa.Float, nullable=False),
            sa.Column("new_qty", sa.Float, nullable=False),
            sa.Column("old_avg_cost", sa.Float, nullable=False),
            sa.Column("new_avg_cost", sa.Float, nullable=False),
            sa.Column("edited_at", sa.DateTime, nullable=False),
        )
        op.create_index("ix_position_edits_user_id", "position_edits", ["user_id"])


def downgrade() -> None:
    if _is_postgres():
        op.execute("DROP INDEX IF EXISTS ix_position_edits_user_id")
        op.execute("DROP TABLE IF EXISTS position_edits")
    else:
        op.drop_index("ix_position_edits_user_id", table_name="position_edits")
        op.drop_table("position_edits")
