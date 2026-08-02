"""Wave 1: persist AI pre-trade advice as DecisionLog rows.

Revision ID: 20260802_0043
Revises: 20260702_0042
Create Date: 2026-08-02

Context
-------
Until now /pretrade output was fire-and-forget: the AI could say AVOID,
the user could buy anyway and lose, and no data recorded any of it.
This migration enables DecisionService.log_pretrade_advice():

  1. decisiontype enum gains 'PRETRADE_ADVICE' (PostgreSQL only —
     SQLite stores enums as VARCHAR and needs no change).
  2. decision_logs.thesis_id becomes nullable, because a pre-trade
     check is most often run for a ticker the user has NO thesis on
     yet. Execution decisions (BUY/SELL/...) always carry a thesis_id.

Backward compatible: existing rows are untouched; nullable=True only
relaxes a constraint.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0043"
down_revision: str = "20260702_0042"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. New enum value — PostgreSQL only. ALTER TYPE ... ADD VALUE cannot
    #    run inside a transaction on PG < 12; Alembic migrations on modern
    #    PG run fine with autocommit_block. On SQLite this is a no-op.
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                "ALTER TYPE decisiontype ADD VALUE IF NOT EXISTS 'PRETRADE_ADVICE'"
            )

    # 2. thesis_id nullable (pre-trade advice may exist without a thesis).
    # batch_alter_table recreates the table on SQLite (which has no native
    # ALTER COLUMN) and degrades to a plain ALTER TABLE on PostgreSQL.
    with op.batch_alter_table("decision_logs") as batch_op:
        batch_op.alter_column(
            "thesis_id",
            existing_type=sa.Integer(),
            nullable=True,
        )


def downgrade() -> None:
    # Remove rows that would violate the restored NOT NULL constraint.
    op.execute("DELETE FROM decision_logs WHERE thesis_id IS NULL")

    with op.batch_alter_table("decision_logs") as batch_op:
        batch_op.alter_column(
            "thesis_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
    # NOTE: PostgreSQL enum values cannot be removed once added;
    # 'PRETRADE_ADVICE' stays in the enum type after downgrade.
