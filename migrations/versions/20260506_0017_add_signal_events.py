"""Add signal_events table.

Revision ID: 20260506_0017
Revises: 20260506_0016
Create Date: 2026-05-06

Context
-------
Wave 3 — Event Bus persistence layer.

The EventBus (platform.event_bus) emits SignalDetectedEvent when the
ScanService detects a tradeable signal. This table persists those events so:

  - ProactiveAlertAgent can query recent signals per user/ticker without
    re-scanning the entire watchlist.
  - Dedup logic has a DB-backed source of truth (in addition to the in-memory
    dedup window on the bus).
  - Dashboard / readmodel can surface signal history.

Schema decisions
----------------
- ``event_id`` (UUID text) — mirrors DomainEvent.event_id for traceability.
- ``signal_type`` (varchar 64) — open string; no enum so new signal types
  don't require a migration.
- ``strength``, ``confidence`` (float) — 0.0-1.0 scores produced by ScanService.
- ``source`` (varchar 32) — "technical" | "news" | "combined".
- ``metadata_json`` (text, nullable) — JSON blob for extra context; avoids
  schema churn as signal types evolve.
- ``processed_at`` (nullable) — set by ProactiveAlertAgent after acting on
  the signal; NULL means pending.
- Composite index on (user_id, ticker, signal_type) for efficient dedup queries.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260506_0017"
down_revision: str = "20260506_0016"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "signal_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("signal_type", sa.String(64), nullable=False),
        sa.Column(
            "strength",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
        sa.Column("source", sa.String(32), nullable=False, server_default="technical"),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_signal_events_event_id"),
    )

    op.create_index("ix_signal_events_user_id", "signal_events", ["user_id"])
    op.create_index("ix_signal_events_ticker", "signal_events", ["ticker"])
    op.create_index("ix_signal_events_signal_type", "signal_events", ["signal_type"])
    op.create_index("ix_signal_events_occurred_at", "signal_events", ["occurred_at"])
    op.create_index("ix_signal_events_processed_at", "signal_events", ["processed_at"])
    op.create_index(
        "ix_signal_events_user_ticker_type",
        "signal_events",
        ["user_id", "ticker", "signal_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_signal_events_user_ticker_type", table_name="signal_events")
    op.drop_index("ix_signal_events_processed_at", table_name="signal_events")
    op.drop_index("ix_signal_events_occurred_at", table_name="signal_events")
    op.drop_index("ix_signal_events_signal_type", table_name="signal_events")
    op.drop_index("ix_signal_events_ticker", table_name="signal_events")
    op.drop_index("ix_signal_events_user_id", table_name="signal_events")
    op.drop_table("signal_events")
