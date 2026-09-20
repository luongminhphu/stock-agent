"""decision_logs: thêm adherence / adherence_action_at — pretrade advice ↔ hành động thật (E3b)

Revision ID: 20260920_0052
Revises: 20260920_0051
Create Date: 2026-09-20

Khi user BUY/SELL thật trong cửa sổ sau một PRETRADE_ADVICE, thesis ghi lên chính row
advice: user đã nghe (followed_advice) hay bỏ qua (ignored_advice). Cho phép scoring
phân biệt "AI đúng nhưng user bỏ qua" với "AI sai".

decision_logs:
  adherence           VARCHAR(16) NULL — followed_advice | ignored_advice
  adherence_action_at TIMESTAMPTZ NULL — thời điểm hành động thật
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260920_0052"
down_revision = "20260920_0051"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgres():
        op.execute("ALTER TABLE decision_logs ADD COLUMN IF NOT EXISTS adherence VARCHAR(16) NULL")
        op.execute(
            "ALTER TABLE decision_logs ADD COLUMN IF NOT EXISTS "
            "adherence_action_at TIMESTAMPTZ NULL"
        )
    else:
        with op.batch_alter_table("decision_logs") as batch:
            batch.add_column(sa.Column("adherence", sa.String(16), nullable=True))
            batch.add_column(
                sa.Column("adherence_action_at", sa.DateTime(timezone=True), nullable=True)
            )


def downgrade() -> None:
    if _is_postgres():
        op.execute("ALTER TABLE decision_logs DROP COLUMN IF EXISTS adherence_action_at")
        op.execute("ALTER TABLE decision_logs DROP COLUMN IF EXISTS adherence")
    else:
        with op.batch_alter_table("decision_logs") as batch:
            batch.drop_column("adherence_action_at")
            batch.drop_column("adherence")
