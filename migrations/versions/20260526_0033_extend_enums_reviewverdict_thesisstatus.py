"""Extend reviewverdict and thesisstatus Postgres enum types.

Revision ID: 20260526_0033
Revises: 20260525_0032
Create Date: 2026-05-26

Gap: Python models define WEAKENING / INVALIDATED / INSUFFICIENT_DATA on
ReviewVerdict and 'weakening' on ThesisStatus, but the Postgres enum types
created in 0001_initial_schema are missing these values.

Safe to run multiple times — IF NOT EXISTS guard prevents duplicate-value errors.
No downgrade: removing enum labels from Postgres requires a full table rebuild
and is not worth the risk on production data.
"""

from alembic import op

revision = "20260526_0033"
down_revision = "20260525_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # reviewverdict                                                         #
    # Initial values (0001): BULLISH, BEARISH, NEUTRAL, WATCHLIST          #
    # Python model adds:     WEAKENING, INVALIDATED, INSUFFICIENT_DATA     #
    # ------------------------------------------------------------------ #
    op.execute("ALTER TYPE reviewverdict ADD VALUE IF NOT EXISTS 'WEAKENING'")
    op.execute("ALTER TYPE reviewverdict ADD VALUE IF NOT EXISTS 'INVALIDATED'")
    op.execute("ALTER TYPE reviewverdict ADD VALUE IF NOT EXISTS 'INSUFFICIENT_DATA'")

    # ------------------------------------------------------------------ #
    # thesisstatus                                                          #
    # Initial values (0001): active, invalidated, closed, paused           #
    # Python model adds:     weakening                                      #
    # ------------------------------------------------------------------ #
    op.execute("ALTER TYPE thesisstatus ADD VALUE IF NOT EXISTS 'weakening'")


def downgrade() -> None:
    # Postgres does not support removing individual enum labels without
    # dropping and recreating the type + all dependent columns.
    # Skipping downgrade intentionally — data safety over rollback convenience.
    pass
