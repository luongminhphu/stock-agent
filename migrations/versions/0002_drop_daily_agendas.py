"""Xoá bảng daily_agendas — đã bỏ tính năng daily agenda.

Revision ID: 0002_drop_daily_agendas
Revises: 0001_baseline
Create Date: 2026-11-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_drop_daily_agendas"
down_revision: str | None = "0001_baseline"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_index(op.f("ix_daily_agendas_user_id"), table_name="daily_agendas")
    op.drop_table("daily_agendas")


def downgrade() -> None:
    op.create_table(
        "daily_agendas",
        sa.Column("id", sa.INTEGER(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column(
            "agenda_date",
            sa.DATE(),
            autoincrement=False,
            nullable=False,
            comment="Date (UTC) this agenda was built for",
        ),
        sa.Column(
            "summary",
            sa.TEXT(),
            autoincrement=False,
            nullable=False,
            comment="Compact multi-line agenda string for Discord embed prefix",
        ),
        sa.Column(
            "buckets_json",
            sa.TEXT(),
            autoincrement=False,
            nullable=True,
            comment="JSON: {decide: [...], watch: [...], defer: [...]}",
        ),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("daily_agendas_pkey")),
        sa.UniqueConstraint("user_id", "agenda_date", name=op.f("uq_daily_agendas_user_date")),
    )
    op.create_index(op.f("ix_daily_agendas_user_id"), "daily_agendas", ["user_id"], unique=False)
