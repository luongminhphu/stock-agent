"""Xoá snapshot của các position đã đóng (mọi ngày).

Revision ID: 20260818_0049
Revises: 20260806_0048
Create Date: 2026-08-18

Wave 7.5: refresh_after_trade(position_closed=True) giờ xoá mọi snapshot
của ticker khi full-sell. Migration này dọn phần data mồ côi TỒN ĐỌNG từ
trước fix: mọi snapshot row mà (user_id, ticker) không còn position đang
mở — bao gồm cả position đã đóng lẫn position đã bị xoá hẳn.

Snapshot là derived read-model — source of truth là positions + trades
(audit trail không bị động vào). EOD job chỉ ghi position đang mở nên các
row bị xoá không bao giờ được tạo lại. Không có consumer nào đọc lịch sử
snapshot của vị thế đã đóng (đã kiểm toàn bộ src/).

Data migration thuần — chạy được trên cả PostgreSQL lẫn SQLite.
"""

from __future__ import annotations

from alembic import op

revision = "20260818_0049"
down_revision = "20260806_0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM position_daily_snapshots
        WHERE NOT EXISTS (
            SELECT 1 FROM positions p
            WHERE p.user_id = position_daily_snapshots.user_id
              AND p.ticker = position_daily_snapshots.ticker
              AND p.closed_at IS NULL
        )
        """
    )


def downgrade() -> None:
    # Không khôi phục: snapshot là derived data, các row đã xoá thuộc về
    # vị thế đã đóng — không cần và không nên tái tạo.
    pass
