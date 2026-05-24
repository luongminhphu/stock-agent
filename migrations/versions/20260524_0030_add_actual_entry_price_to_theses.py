"""add actual_entry_price to theses

Revision ID: 20260524_0030
Revises: 20260524_0029
Create Date: 2026-05-24

Rationale:
  Separates thesis reference price (entry_price) from the real execution
  price (actual_entry_price). actual_entry_price is backfilled automatically
  when /buy executes and links to a thesis_id. Only set on first buy —
  subsequent avg-down trades do not overwrite it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260524_0030"
down_revision: str | None = "20260524_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "theses",
        sa.Column("actual_entry_price", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("theses", "actual_entry_price")
