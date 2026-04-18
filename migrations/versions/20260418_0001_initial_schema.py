"""initial schema

Revision ID: 0001_initial
Revises: -
Create Date: 2026-04-18 00:00:00 UTC

Tables created:
    theses                 — thesis lifecycle (thesis segment)
    assumptions            — thesis assumptions (thesis segment)
    catalysts              — thesis catalysts (thesis segment)
    thesis_reviews         — AI review snapshots (thesis segment)
    thesis_snapshots       — point-in-time PnL snapshots (thesis segment)
    watchlist_items        — user watchlist (watchlist segment)
    alerts                 — price/volume alerts (watchlist segment)
    reminders              — notification reminders (watchlist segment)
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # theses
    # ------------------------------------------------------------------
    op.create_table(
        "theses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("active", "invalidated", "closed", "paused", name="thesisstatus"),
            nullable=False,
        ),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("target_price", sa.Float(), nullable=True),
        sa.Column("stop_loss", sa.Float(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_theses_user_id", "theses", ["user_id"])
    op.create_index("ix_theses_ticker", "theses", ["ticker"])

    # ------------------------------------------------------------------
    # assumptions
    # ------------------------------------------------------------------
    op.create_table(
        "assumptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thesis_id", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("valid", "invalid", "uncertain", "pending", name="assumptionstatus"),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["thesis_id"], ["theses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assumptions_thesis_id", "assumptions", ["thesis_id"])

    # ------------------------------------------------------------------
    # catalysts
    # ------------------------------------------------------------------
    op.create_table(
        "catalysts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thesis_id", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "triggered", "expired", "cancelled", name="catalyststatus"),
            nullable=False,
        ),
        sa.Column("expected_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["thesis_id"], ["theses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_catalysts_thesis_id", "catalysts", ["thesis_id"])

    # ------------------------------------------------------------------
    # thesis_reviews
    # ------------------------------------------------------------------
    op.create_table(
        "thesis_reviews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thesis_id", sa.Integer(), nullable=False),
        sa.Column(
            "verdict",
            sa.Enum("BULLISH", "BEARISH", "NEUTRAL", "WATCHLIST", name="reviewverdict"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("risk_signals", sa.Text(), nullable=True),
        sa.Column("next_watch_items", sa.Text(), nullable=True),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("reviewed_price", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["thesis_id"], ["theses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_thesis_reviews_thesis_id", "thesis_reviews", ["thesis_id"])

    # ------------------------------------------------------------------
    # thesis_snapshots
    # ------------------------------------------------------------------
    op.create_table(
        "thesis_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thesis_id", sa.Integer(), nullable=False),
        sa.Column("price_at_snapshot", sa.Float(), nullable=False),
        sa.Column("pnl_pct", sa.Float(), nullable=True),
        sa.Column("score_at_snapshot", sa.Float(), nullable=True),
        sa.Column(
            "snapshotted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["thesis_id"], ["theses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_thesis_snapshots_thesis_id", "thesis_snapshots", ["thesis_id"])
    # Composite index for leaderboard queries: latest snapshot per thesis
    op.create_index(
        "ix_thesis_snapshots_thesis_snapshotted",
        "thesis_snapshots",
        ["thesis_id", "snapshotted_at"],
    )

    # ------------------------------------------------------------------
    # watchlist_items
    # ------------------------------------------------------------------
    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("thesis_id", sa.Integer(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "ticker", name="uq_watchlist_user_ticker"),
    )
    op.create_index("ix_watchlist_items_user_id", "watchlist_items", ["user_id"])
    op.create_index("ix_watchlist_items_thesis_id", "watchlist_items", ["thesis_id"])

    # ------------------------------------------------------------------
    # alerts
    # ------------------------------------------------------------------
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column("watchlist_item_id", sa.Integer(), nullable=True),
        sa.Column(
            "condition_type",
            sa.Enum(
                "price_above",
                "price_below",
                "change_pct_up",
                "change_pct_down",
                "volume_spike",
                name="alertconditiontype",
            ),
            nullable=False,
        ),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "triggered", "dismissed", "expired", name="alertstatus"),
            nullable=False,
        ),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_price", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["watchlist_item_id"], ["watchlist_items.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alerts_user_id", "alerts", ["user_id"])
    op.create_index("ix_alerts_watchlist_item_id", "alerts", ["watchlist_item_id"])

    # ------------------------------------------------------------------
    # reminders
    # ------------------------------------------------------------------
    op.create_table(
        "reminders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("watchlist_item_id", sa.Integer(), nullable=False),
        sa.Column(
            "frequency",
            sa.Enum("daily", "weekly", "on_signal", name="reminderfrequency"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["watchlist_item_id"], ["watchlist_items.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reminders_user_id", "reminders", ["user_id"])


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("reminders")
    op.drop_table("alerts")
    op.drop_table("watchlist_items")
    op.drop_table("thesis_snapshots")
    op.drop_table("thesis_reviews")
    op.drop_table("catalysts")
    op.drop_table("assumptions")
    op.drop_table("theses")

    # Drop enum types (PostgreSQL)
    op.execute("DROP TYPE IF EXISTS thesisstatus")
    op.execute("DROP TYPE IF EXISTS assumptionstatus")
    op.execute("DROP TYPE IF EXISTS catalyststatus")
    op.execute("DROP TYPE IF EXISTS reviewverdict")
    op.execute("DROP TYPE IF EXISTS alertconditiontype")
    op.execute("DROP TYPE IF EXISTS alertstatus")
    op.execute("DROP TYPE IF EXISTS reminderfrequency")
