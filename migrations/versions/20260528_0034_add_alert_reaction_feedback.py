"""Add reaction feedback columns to alerts table.

Wave D: adaptive cooldown loop.

New columns:
    reaction_count         INT NOT NULL DEFAULT 0
    dismiss_count          INT NOT NULL DEFAULT 0
    effective_cooldown_hours  INT NULL

All columns are nullable/defaulted so existing rows are unaffected.
No enum changes, no FK changes.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260528_0034"
down_revision = "20260526_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alerts",
        sa.Column("reaction_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "alerts",
        sa.Column("dismiss_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "alerts",
        sa.Column("effective_cooldown_hours", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("alerts", "effective_cooldown_hours")
    op.drop_column("alerts", "dismiss_count")
    op.drop_column("alerts", "reaction_count")
