"""decision_logs: advice_id / advice_action / advice_proceed — nối lệnh với lời khuyên (P2b)

Revision ID: 20261010_0054
Revises: 20261010_0053
Create Date: 2026-10-10

Trước đây adherence nối hành động thật với PRETRADE_ADVICE gần nhất trong cửa sổ 3 ngày
theo verdict BULLISH/BEARISH. P2b: row thực thi mang advice_id của đúng lời khuyên đã xem;
row PRETRADE_ADVICE lưu lệnh dự kiến (advice_action) và AI có khuyến nghị thực hiện
không (advice_proceed) → rule followed/ignored chính xác theo từng lệnh.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261010_0054"
down_revision = "20261010_0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "decision_logs",
        sa.Column(
            "advice_id",
            sa.Integer(),
            sa.ForeignKey("decision_logs.id", ondelete="SET NULL"),
            nullable=True,
            comment="P2b: PRETRADE_ADVICE mà quyết định này dựa vào",
        ),
    )
    op.create_index("ix_decision_logs_advice_id", "decision_logs", ["advice_id"])
    op.add_column(
        "decision_logs",
        sa.Column(
            "advice_action",
            sa.String(length=10),
            nullable=True,
            comment="P2b: lệnh dự kiến khi hỏi AI (BUY/SELL/REDUCE/HOLD) — chỉ row PRETRADE_ADVICE",
        ),
    )
    op.add_column(
        "decision_logs",
        sa.Column(
            "advice_proceed",
            sa.Boolean(),
            nullable=True,
            comment="P2b: AI khuyến nghị thực hiện lệnh dự kiến không — chỉ row PRETRADE_ADVICE",
        ),
    )


def downgrade() -> None:
    op.drop_column("decision_logs", "advice_proceed")
    op.drop_column("decision_logs", "advice_action")
    op.drop_index("ix_decision_logs_advice_id", table_name="decision_logs")
    op.drop_column("decision_logs", "advice_id")
