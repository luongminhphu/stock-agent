"""safe schema drift cleanup

Revision ID: 20260507_0019
Revises: 20260507_0018
Create Date: 2026-05-07

Fixes model/DB drift detected by alembic autogenerate.
Only safe, non-destructive changes are included here.
Destructive changes (DROP TABLE, DROP COLUMN) are deferred to 0020.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260507_0019"
down_revision = "20260507_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. theses.ticker: VARCHAR(10) → VARCHAR(20)
    # ------------------------------------------------------------------
    op.alter_column(
        "theses",
        "ticker",
        existing_type=sa.VARCHAR(length=10),
        type_=sa.String(length=20),
        existing_nullable=False,
    )

    # ------------------------------------------------------------------
    # 2. theses.title: VARCHAR(256) → VARCHAR(255)
    # ------------------------------------------------------------------
    op.alter_column(
        "theses",
        "title",
        existing_type=sa.VARCHAR(length=256),
        type_=sa.String(length=255),
        existing_nullable=False,
    )

    # ------------------------------------------------------------------
    # 3. theses.direction: VARCHAR(16) → Enum
    #    Safe path: add enum type if not exists, then alter column.
    # ------------------------------------------------------------------
    thesisdirection = sa.Enum(
        "LONG", "SHORT", "NEUTRAL",
        name="thesisdirection",
        create_constraint=False,
    )
    # Create enum type in PG (IF NOT EXISTS via checkfirst)
    thesisdirection.create(op.get_bind(), checkfirst=True)
    op.alter_column(
        "theses",
        "direction",
        existing_type=sa.VARCHAR(length=16),
        type_=thesisdirection,
        existing_nullable=True,
        postgresql_using="direction::thesisdirection",
    )

    # ------------------------------------------------------------------
    # 4. decision_logs.outcome_verdict: fix enum type name typo
    #    outcomeoverdict → outcomeverdict
    #    PostgreSQL: rename the type directly.
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TYPE outcomeoverdict RENAME TO outcomeverdict"
    )

    # ------------------------------------------------------------------
    # 5. Add missing indexes
    # ------------------------------------------------------------------
    op.create_index("ix_assumptions_status", "assumptions", ["status"])
    op.create_index("ix_catalysts_status", "catalysts", ["status"])
    op.create_index("ix_decision_logs_decision_type", "decision_logs", ["decision_type"])
    op.create_index("ix_theses_created_at", "theses", ["created_at"])
    op.create_index("ix_theses_status", "theses", ["status"])
    op.create_index("ix_thesis_reviews_reviewed_at", "thesis_reviews", ["reviewed_at"])
    op.create_index("ix_thesis_reviews_verdict", "thesis_reviews", ["verdict"])
    op.create_index(
        "ix_thesis_snapshots_snapshotted_at", "thesis_snapshots", ["snapshotted_at"]
    )

    # ------------------------------------------------------------------
    # 6. Remove stale signal_events indexes superseded by migration 0017
    # ------------------------------------------------------------------
    op.drop_index(
        "ix_signal_events_occurred_at",
        table_name="signal_events",
        if_exists=True,
    )
    op.drop_index(
        "ix_signal_events_processed_at",
        table_name="signal_events",
        if_exists=True,
    )
    op.drop_index(
        "ix_signal_events_user_ticker_type",
        table_name="signal_events",
        if_exists=True,
    )


def downgrade() -> None:
    # Restore stale signal_events indexes
    op.create_index(
        "ix_signal_events_user_ticker_type",
        "signal_events",
        ["user_id", "ticker", "signal_type"],
    )
    op.create_index(
        "ix_signal_events_processed_at", "signal_events", ["processed_at"]
    )
    op.create_index(
        "ix_signal_events_occurred_at", "signal_events", ["occurred_at"]
    )

    # Drop added indexes
    op.drop_index("ix_thesis_snapshots_snapshotted_at", table_name="thesis_snapshots")
    op.drop_index("ix_thesis_reviews_verdict", table_name="thesis_reviews")
    op.drop_index("ix_thesis_reviews_reviewed_at", table_name="thesis_reviews")
    op.drop_index("ix_theses_status", table_name="theses")
    op.drop_index("ix_theses_created_at", table_name="theses")
    op.drop_index("ix_decision_logs_decision_type", table_name="decision_logs")
    op.drop_index("ix_catalysts_status", table_name="catalysts")
    op.drop_index("ix_assumptions_status", table_name="assumptions")

    # Rename enum back
    op.execute("ALTER TYPE outcomeverdict RENAME TO outcomeoverdict")

    # Revert direction to VARCHAR
    op.alter_column(
        "theses",
        "direction",
        existing_type=sa.Enum(
            "LONG", "SHORT", "NEUTRAL", name="thesisdirection"
        ),
        type_=sa.VARCHAR(length=16),
        existing_nullable=True,
        postgresql_using="direction::varchar",
    )

    # Revert title and ticker
    op.alter_column(
        "theses",
        "title",
        existing_type=sa.String(length=255),
        type_=sa.VARCHAR(length=256),
        existing_nullable=False,
    )
    op.alter_column(
        "theses",
        "ticker",
        existing_type=sa.String(length=20),
        type_=sa.VARCHAR(length=10),
        existing_nullable=False,
    )
