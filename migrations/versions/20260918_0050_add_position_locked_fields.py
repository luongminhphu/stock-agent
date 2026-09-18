"""add positions.locked_* + position_edits locked audit columns

Revision ID: 20260918_0050
Revises: 20260818_0049
Create Date: 2026-09-18

Wave 9.1 — locked position (ESOP / phat hanh rieng le / CP thuong dang cho
ve / cam co...) — mot phan hoac toan bo vi the khong ban duoc.

positions:
  locked_qty    FLOAT NOT NULL DEFAULT 0  — 0 = toan bo ban duoc (backward compat)
  locked_reason VARCHAR(64) NULL          — esop|private_placement|pending_settlement|
                                            pledged|odd_lot|core_hold|free text
  locked_until  DATE NULL                 — ngay du kien mo khoa, NULL = khong ro

position_edits:
  6 cot audit old/new cho locked_* (NULL = edit do khong dong toi lock).

Postgres path: raw SQL idempotent (ADD COLUMN IF NOT EXISTS).
SQLite path: batch_alter_table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260918_0050"
down_revision = "20260818_0049"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgres():
        op.execute(
            "ALTER TABLE positions ADD COLUMN IF NOT EXISTS "
            "locked_qty DOUBLE PRECISION NOT NULL DEFAULT 0"
        )
        op.execute(
            "ALTER TABLE positions ADD COLUMN IF NOT EXISTS "
            "locked_reason VARCHAR(64) NULL"
        )
        op.execute(
            "ALTER TABLE positions ADD COLUMN IF NOT EXISTS "
            "locked_until DATE NULL"
        )
        for col, typ in (
            ("old_locked_qty", "DOUBLE PRECISION"),
            ("new_locked_qty", "DOUBLE PRECISION"),
            ("old_locked_reason", "VARCHAR(64)"),
            ("new_locked_reason", "VARCHAR(64)"),
            ("old_locked_until", "DATE"),
            ("new_locked_until", "DATE"),
        ):
            op.execute(
                f"ALTER TABLE position_edits ADD COLUMN IF NOT EXISTS {col} {typ} NULL"
            )
    else:
        with op.batch_alter_table("positions") as batch:
            batch.add_column(
                sa.Column("locked_qty", sa.Float(), nullable=False, server_default="0")
            )
            batch.add_column(sa.Column("locked_reason", sa.String(64), nullable=True))
            batch.add_column(sa.Column("locked_until", sa.Date(), nullable=True))
        with op.batch_alter_table("position_edits") as batch:
            batch.add_column(sa.Column("old_locked_qty", sa.Float(), nullable=True))
            batch.add_column(sa.Column("new_locked_qty", sa.Float(), nullable=True))
            batch.add_column(
                sa.Column("old_locked_reason", sa.String(64), nullable=True)
            )
            batch.add_column(
                sa.Column("new_locked_reason", sa.String(64), nullable=True)
            )
            batch.add_column(sa.Column("old_locked_until", sa.Date(), nullable=True))
            batch.add_column(sa.Column("new_locked_until", sa.Date(), nullable=True))


def downgrade() -> None:
    if _is_postgres():
        for col in (
            "new_locked_until", "old_locked_until",
            "new_locked_reason", "old_locked_reason",
            "new_locked_qty", "old_locked_qty",
        ):
            op.execute(f"ALTER TABLE position_edits DROP COLUMN IF EXISTS {col}")
        op.execute("ALTER TABLE positions DROP COLUMN IF EXISTS locked_until")
        op.execute("ALTER TABLE positions DROP COLUMN IF EXISTS locked_reason")
        op.execute("ALTER TABLE positions DROP COLUMN IF EXISTS locked_qty")
    else:
        with op.batch_alter_table("position_edits") as batch:
            for col in (
                "new_locked_until", "old_locked_until",
                "new_locked_reason", "old_locked_reason",
                "new_locked_qty", "old_locked_qty",
            ):
                batch.drop_column(col)
        with op.batch_alter_table("positions") as batch:
            batch.drop_column("locked_until")
            batch.drop_column("locked_reason")
            batch.drop_column("locked_qty")
