"""add trades.exit_reason + trades.entry_signal_ref

Revision ID: 20260806_0045
Revises: 20260802_0044
Create Date: 2026-08-06

Model Trade đã có 2 cột này (ExitReason enum + entry_signal_ref) nhưng không
có migration nào tạo chúng — /history trên production fail:
  asyncpg.exceptions.UndefinedColumnError: column trades.exit_reason does not exist

Postgres path dùng raw SQL idempotent (enum có thể đã tồn tại ngoài Alembic,
giống case dividendtype của 0044). SQLite path dùng batch_alter_table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260806_0045"
down_revision = "20260802_0044"
branch_labels = None
depends_on = None

_EXIT_REASON_VALUES = (
    "stop_loss", "target_hit", "thesis_invalidated", "risk_limit",
    "rebalance", "manual",
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgres():
        # Enum exitreason: tạo nếu chưa có (không re-emit nếu đã tồn tại)
        op.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'exitreason') THEN
                    CREATE TYPE exitreason AS ENUM ('stop_loss','target_hit','thesis_invalidated','risk_limit','rebalance','manual');
                END IF;
            END
            $$;
            """
        )
        op.execute(
            "ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_reason exitreason NULL"
        )
        op.execute(
            "ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_signal_ref VARCHAR(64) NULL"
        )
    else:
        with op.batch_alter_table("trades") as batch:
            batch.add_column(
                sa.Column(
                    "exit_reason",
                    sa.Enum(*_EXIT_REASON_VALUES, name="exitreason"),
                    nullable=True,
                )
            )
            batch.add_column(
                sa.Column("entry_signal_ref", sa.String(64), nullable=True)
            )


def downgrade() -> None:
    if _is_postgres():
        op.execute("ALTER TABLE trades DROP COLUMN IF EXISTS entry_signal_ref")
        op.execute("ALTER TABLE trades DROP COLUMN IF EXISTS exit_reason")
        op.execute("DROP TYPE IF EXISTS exitreason")
    else:
        with op.batch_alter_table("trades") as batch:
            batch.drop_column("entry_signal_ref")
            batch.drop_column("exit_reason")
