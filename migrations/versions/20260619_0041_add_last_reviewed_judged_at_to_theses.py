"""Add last_reviewed_at and last_judged_at to theses table.

Revision ID: 20260619_0041
Revises: 20260611_0040
Create Date: 2026-06-19

Problem:
    Wave 4 (thesis_judge batch dedup) introduced skip logic based on
    ThesisJudgeTrigger.last_reviewed_at and .last_judged_at. However:

    1. `last_reviewed_at` did NOT exist as a column on `theses` — it only
       existed as a transient computed field on ThesisRef (Pydantic schema).
       Source of truth is MAX(thesis_reviews.reviewed_at) per thesis_id.

    2. `last_judged_at` was only set on in-memory ThesisJudgeOutput.judged_at —
       never persisted, so Wave 4 dedup guard was always falling through to
       "judge everything" (safe default, but defeats the optimization).

    Without these columns:
    - judge() wrapper cannot pass last_reviewed_at to ThesisJudgeTrigger
    - Snapshot stale detection computes last_reviewed_at from a correlated
      subquery on every call (extra JOIN per thesis per briefing cycle)
    - ThesisService.touch_reviewed_at() only updates Thesis.updated_at —
      readmodel and dedup guard both miss the review event

Solution:
    A. Add theses.last_reviewed_at TIMESTAMPTZ — maintained by:
         - ThesisService.touch_reviewed_at() (already updates updated_at,
           now also sets last_reviewed_at)
         - ThesisReviewService.create_review() already sets ThesisReview.reviewed_at —
           caller should also update Thesis.last_reviewed_at
       Backfilled from: MAX(thesis_reviews.reviewed_at) per thesis.

    B. Add theses.last_judged_at TIMESTAMPTZ — maintained by:
         - _log_thesis_judge_interaction() in thesis_judge.py: already called on
           every verdict, now also writes Thesis.last_judged_at.
       Default: NULL (no historical judge data before this migration).

    C. Add covering index on (user_id, status, last_reviewed_at) to support
       the stale-thesis query in SystemSnapshotBuilder without full table scan.

Post-migration steps (manual, after applying):
    1. Run backfill: src/ai/agents/thesis_judge_audit.py:backfill_last_reviewed_at()
    2. Update ThesisService.touch_reviewed_at() to also set last_reviewed_at
    3. Update _log_thesis_judge_interaction() to write last_judged_at
    4. Update ThesisJudgeAgent.judge() wrapper to pass last_reviewed_at from ORM

Schema change:
    theses (
        ...existing columns...
        + last_reviewed_at   TIMESTAMPTZ NULL   -- from thesis_reviews, maintained by thesis svc
        + last_judged_at     TIMESTAMPTZ NULL   -- from ThesisJudgeAgent, maintained by ai svc
    )
    + INDEX ix_theses_user_status_last_reviewed (user_id, status, last_reviewed_at)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision = "20260619_0041"
down_revision = "20260611_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A. Add last_reviewed_at — nullable TIMESTAMPTZ, backfilled separately
    op.add_column(
        "theses",
        sa.Column(
            "last_reviewed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="MAX(thesis_reviews.reviewed_at) for this thesis. "
                    "Maintained by ThesisService.touch_reviewed_at() and "
                    "ThesisReviewService.create_review(). "
                    "Used by: snapshot stale detection, Wave 4 dedup guard.",
        ),
    )

    # B. Add last_judged_at — nullable TIMESTAMPTZ, starts as NULL
    op.add_column(
        "theses",
        sa.Column(
            "last_judged_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp of last ThesisJudgeAgent verdict for this thesis. "
                    "Maintained by ai._log_thesis_judge_interaction(). "
                    "Used by: Wave 4 dedup guard in ThesisJudgeAgent.run_batch().",
        ),
    )

    # C. Covering index for stale-thesis query pattern:
    #    WHERE user_id = ? AND status = 'active' ORDER BY last_reviewed_at ASC
    op.create_index(
        "ix_theses_user_status_last_reviewed",
        "theses",
        ["user_id", "status", "last_reviewed_at"],
        postgresql_where=sa.text("status = 'active'"),
    )

    # D. Backfill last_reviewed_at from existing ThesisReview data in same transaction.
    #    This is a one-time operation; subsequent updates go through service layer.
    op.execute(sa.text("""
        UPDATE theses t
        SET last_reviewed_at = sub.max_reviewed_at
        FROM (
            SELECT
                thesis_id,
                MAX(reviewed_at) AS max_reviewed_at
            FROM thesis_reviews
            GROUP BY thesis_id
        ) sub
        WHERE t.id = sub.thesis_id
    """))


def downgrade() -> None:
    op.drop_index("ix_theses_user_status_last_reviewed", table_name="theses")
    op.drop_column("theses", "last_judged_at")
    op.drop_column("theses", "last_reviewed_at")
