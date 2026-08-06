"""position_edits.edited_at → TIMESTAMPTZ

Revision ID: 20260806_0048
Revises: 20260806_0047
Create Date: 2026-08-06

0047 khai báo DateTime(timezone=False) nhưng edit_position() truyền
datetime.now(UTC) (aware) → asyncpg DataError "can't subtract offset-naive
and offset-aware". Mọi bảng khác đều TIMESTAMPTZ — align lại. Bảng mới,
chưa có data production nên ALTER an toàn.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260806_0048"
down_revision = "20260806_0047"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgres():
        op.execute(
            "ALTER TABLE position_edits ALTER COLUMN edited_at "
            "TYPE TIMESTAMPTZ USING edited_at AT TIME ZONE 'UTC'"
        )
    else:
        with op.batch_alter_table("position_edits") as batch:
            batch.alter_column(
                "edited_at",
                existing_type=sa.DateTime,
                type_=sa.DateTime(timezone=True),
                existing_nullable=False,
            )


def downgrade() -> None:
    if _is_postgres():
        op.execute(
            "ALTER TABLE position_edits ALTER COLUMN edited_at "
            "TYPE TIMESTAMP USING edited_at AT TIME ZONE 'UTC'"
        )
    else:
        with op.batch_alter_table("position_edits") as batch:
            batch.alter_column(
                "edited_at",
                existing_type=sa.DateTime(timezone=True),
                type_=sa.DateTime,
                existing_nullable=False,
            )
