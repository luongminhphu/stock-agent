"""add snoozed_until to watchlist_items

Revision ID: 20260604_0037
Revises: 20260604_0036
Create Date: 2026-06-04

Covers: WatchlistItem.snoozed_until column added in Wave A
(feat(watchlist): add WatchlistItem.snoozed_until column + WatchlistService.snooze())

Owner: watchlist segment.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260604_0037"
down_revision = "20260604_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "watchlist_items",
        sa.Column(
            "snoozed_until",
            sa.DateTime(timezone=True),
            nullable=True,
            default=None,
        ),
    )


def downgrade() -> None:
    op.drop_column("watchlist_items", "snoozed_until")
