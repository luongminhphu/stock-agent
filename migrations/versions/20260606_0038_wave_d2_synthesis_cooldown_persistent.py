"""Wave D.2 — Persistent pattern synthesis cooldown guard.

Revision ID: 20260606_0038
Revises: 20260606_0037
Create Date: 2026-06-06

Problem:
    _SYNTHESIS_COOLDOWN_TS was a module-level dict (added in fix(ai): b64da1d).
    On every restart this dict is reset, so the 60-minute cooldown guard
    was ineffective after a restart — multiple agents calling ContextBuilder
    concurrently in the warm-up window could each trigger synthesize_patterns(),
    resulting in N redundant AI calls.

Solution:
    Store last_synthesis_at as a column on memory_snapshots (latest row per user).
    ContextBuilder reads this timestamp instead of the in-memory dict.
    After synthesis: UPDATE memory_snapshots SET last_synthesis_at = NOW()
    where id = latest_snapshot.id.
    Cooldown survives restarts because the timestamp lives in the DB.

Change:
    memory_snapshots: ADD COLUMN last_synthesis_at TIMESTAMPTZ NULL
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260606_0038"
down_revision = "20260606_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memory_snapshots",
        sa.Column(
            "last_synthesis_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp of last successful synthesize_patterns() call — Wave D.2 cooldown guard",
        ),
    )


def downgrade() -> None:
    op.drop_column("memory_snapshots", "last_synthesis_at")
