"""theses/assumptions: cột cho "Kế hoạch nắm giữ"

Revision ID: 20261111_0056
Revises: 20261111_0055
Create Date: 2026-11-11

- assumptions.check_key: mẫu "điều tôi tin" kiểm được bằng số liệu (NULL = tự ghi).
- theses.review_by: hạn xem lại; mặc định 6 tháng sau ngày tạo cho kế hoạch đang mở.
- theses.plan_light / plan_summary / plan_reasons / plan_checked_at: kết quả lần kiểm
  tra kế hoạch gần nhất (đèn xanh/vàng/đỏ/xám thay cho điểm 0–100).
Không xoá cột cũ (score, …) — dữ liệu cũ giữ nguyên.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261111_0056"
down_revision = "20261111_0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assumptions", sa.Column("check_key", sa.String(length=32), nullable=True))
    op.add_column("theses", sa.Column("review_by", sa.Date(), nullable=True))
    op.add_column("theses", sa.Column("plan_light", sa.String(length=8), nullable=True))
    op.add_column("theses", sa.Column("plan_summary", sa.Text(), nullable=True))
    op.add_column("theses", sa.Column("plan_reasons", sa.Text(), nullable=True))
    op.add_column("theses", sa.Column("plan_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_theses_plan_light", "theses", ["plan_light"])
    # Kế hoạch đang mở chưa có hạn xem lại → 6 tháng sau ngày tạo (dialect-neutral qua Python)
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, created_at FROM theses WHERE review_by IS NULL AND closed_at IS NULL")
    ).fetchall()
    from datetime import date, datetime, timedelta

    for tid, created in rows:
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        base = created.date() if isinstance(created, datetime) else date.today()
        conn.execute(
            sa.text("UPDATE theses SET review_by = :d WHERE id = :i"),
            {"d": base + timedelta(days=182), "i": tid},
        )


def downgrade() -> None:
    op.drop_index("ix_theses_plan_light", table_name="theses")
    for col in ("plan_checked_at", "plan_reasons", "plan_summary", "plan_light", "review_by"):
        op.drop_column("theses", col)
    op.drop_column("assumptions", "check_key")
