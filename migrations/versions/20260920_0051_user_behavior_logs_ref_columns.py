"""user_behavior_logs: thêm ref_type / ref_id — feedback ledger hợp nhất (Wave E3a)

Revision ID: 20260920_0051
Revises: 20260918_0050
Create Date: 2026-09-20

S6 — mọi phản hồi của nhà đầu tư (verdict core, brief, pretrade adherence, user action)
đổ về một ledger ``user_behavior_logs`` để ai.memory và readmodel đọc 1 nguồn.

user_behavior_logs:
  ref_type VARCHAR(16) NULL  — verdict | brief | pretrade | thesis
  ref_id   VARCHAR(64) NULL  — id của đối tượng được phản hồi (verdict_event_id,
                               brief_snapshot_id, decision_log_id, thesis_id)
  index ix_user_behavior_logs_ref (ref_type, ref_id)

Bảng core_feedback / brief_feedback giữ nguyên (dual-write, retire ở wave sau).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260920_0051"
down_revision = "20260918_0050"
branch_labels = None
depends_on = None

_INDEX = "ix_user_behavior_logs_ref"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgres():
        op.execute(
            "ALTER TABLE user_behavior_logs ADD COLUMN IF NOT EXISTS ref_type VARCHAR(16) NULL"
        )
        op.execute(
            "ALTER TABLE user_behavior_logs ADD COLUMN IF NOT EXISTS ref_id VARCHAR(64) NULL"
        )
        op.execute(f"CREATE INDEX IF NOT EXISTS {_INDEX} ON user_behavior_logs (ref_type, ref_id)")
    else:
        with op.batch_alter_table("user_behavior_logs") as batch:
            batch.add_column(sa.Column("ref_type", sa.String(16), nullable=True))
            batch.add_column(sa.Column("ref_id", sa.String(64), nullable=True))
        op.create_index(_INDEX, "user_behavior_logs", ["ref_type", "ref_id"])


def downgrade() -> None:
    if _is_postgres():
        op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
        op.execute("ALTER TABLE user_behavior_logs DROP COLUMN IF EXISTS ref_id")
        op.execute("ALTER TABLE user_behavior_logs DROP COLUMN IF EXISTS ref_type")
    else:
        op.drop_index(_INDEX, table_name="user_behavior_logs")
        with op.batch_alter_table("user_behavior_logs") as batch:
            batch.drop_column("ref_id")
            batch.drop_column("ref_type")
