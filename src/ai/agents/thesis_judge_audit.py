"""thesis_judge_audit.py — Audit + repair timestamp consistency for thesis × watchlist.

Owner: ai segment (audit tooling).

Problem identified after Wave 4 (batch dedup):
  Wave 4 dedup logic skips triggers where last_reviewed_at OR last_judged_at
  is within 30 minutes and signal_context is empty. However:

  1. `last_reviewed_at` does NOT exist as a column on the `Thesis` ORM model —
     it only lives on `ThesisRef` (a transient Pydantic schema computed from
     the latest `ThesisReview.created_at` at query time in snapshot.py).

  2. `last_judged_at` is set on `ThesisJudgeOutput.judged_at` (an in-memory
     AI output) — but is NOT persisted back to any DB table, so the value is
     lost on every process restart.

  3. The `judge()` convenience wrapper in ThesisJudgeAgent builds triggers
     WITHOUT populating `last_reviewed_at` or `last_judged_at`, so the Wave 4
     dedup guard always falls through to "judge everything" (safe default but
     defeats the optimization).

Findings:
  - Thesis table: has `updated_at` (auto-updated on any field change) and
    `created_at`, but NO `last_reviewed_at` column.
  - ThesisReview table: has `reviewed_at` (the authoritative timestamp for
    user-triggered reviews) and `created_at`.
  - ThesisJudgeOutput: `judged_at` is a string field set in-memory — not
    written to DB anywhere in the current codebase.
  - WatchlistItem: has `added_at`, `updated_at`, `snoozed_until` — no
    review/judge timestamp.

Required fixes (this module implements):
  A. Add `last_reviewed_at` column to Thesis table via Alembic migration.
     Source of truth: MAX(ThesisReview.reviewed_at) per thesis_id.
     Updated by: ThesisService.touch_reviewed_at() (already exists, only
     updates Thesis.updated_at — needs to also set last_reviewed_at).

  B. Add `last_judged_at` column to Thesis table via Alembic migration.
     Source of truth: timestamp of last ThesisJudgeAgent verdict for this thesis.
     Updated by: _log_thesis_judge_interaction() — already called on every
     verdict, just needs to write to Thesis.last_judged_at.

  C. Backfill Thesis.last_reviewed_at from MAX(ThesisReview.reviewed_at).
     Backfill Thesis.last_judged_at = NULL (no historical judge data).

  D. Update judge() wrapper to populate last_reviewed_at from Thesis ORM
     attribute so Wave 4 dedup actually fires.

Segment boundary:
  - Migration: platform (schema concern)
  - Backfill: platform (data repair)
  - last_reviewed_at write: thesis (touch_reviewed_at already owns this)
  - last_judged_at write: ai (thesis_judge memory logger owns this)
  - judge() wrapper fix: ai segment

This file contains:
  - Audit queries (read-only, run against live DB to assess state)
  - Repair helpers (backfill data after migration is applied)
  - No side effects on import
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# Audit Queries (pure SQL / SQLAlchemy — no ORM imports to keep this portable)
# ---------------------------------------------------------------------------

AUDIT_STALE_THESIS_SQL = """
-- Theses with last_reviewed_at older than 48h (or never reviewed)
-- that were NOT triggered by the system in that window.
-- Source of truth: ThesisReview.reviewed_at (not Thesis.updated_at).
SELECT
    t.id                                        AS thesis_id,
    t.ticker,
    t.user_id,
    t.status,
    t.created_at,
    t.updated_at,
    MAX(tr.reviewed_at)                         AS last_reviewed_at,
    CASE
        WHEN MAX(tr.reviewed_at) IS NULL THEN 9999
        ELSE EXTRACT(EPOCH FROM (NOW() - MAX(tr.reviewed_at))) / 3600
    END                                         AS hours_since_review,
    COUNT(tr.id)                                AS review_count
FROM theses t
LEFT JOIN thesis_reviews tr ON tr.thesis_id = t.id
WHERE t.status = 'active'
GROUP BY t.id, t.ticker, t.user_id, t.status, t.created_at, t.updated_at
HAVING
    MAX(tr.reviewed_at) IS NULL
    OR MAX(tr.reviewed_at) < NOW() - INTERVAL '48 hours'
ORDER BY hours_since_review DESC;
"""

AUDIT_MISSING_LAST_REVIEWED_AT_COL_SQL = """
-- Check whether last_reviewed_at column exists on theses table.
SELECT column_name
FROM information_schema.columns
WHERE table_name = 'theses'
  AND column_name IN ('last_reviewed_at', 'last_judged_at');
"""

AUDIT_WATCHLIST_WITHOUT_THESIS_SQL = """
-- WatchlistItems that have a ticker with an active thesis
-- but the thesis has no review in the last 48h.
-- Useful to identify tickers that should have auto-triggered but didn't.
SELECT
    wi.user_id,
    wi.ticker,
    wi.added_at,
    wi.updated_at,
    t.id                AS thesis_id,
    MAX(tr.reviewed_at) AS thesis_last_reviewed_at
FROM watchlist_items wi
JOIN theses t
    ON t.ticker = wi.ticker
    AND t.user_id = wi.user_id
    AND t.status = 'active'
LEFT JOIN thesis_reviews tr ON tr.thesis_id = t.id
GROUP BY wi.user_id, wi.ticker, wi.added_at, wi.updated_at, t.id
HAVING
    MAX(tr.reviewed_at) IS NULL
    OR MAX(tr.reviewed_at) < NOW() - INTERVAL '48 hours'
ORDER BY wi.user_id, wi.ticker;
"""

AUDIT_DEDUP_GUARD_EFFECTIVENESS_SQL = """
-- Shows how many theses would be SKIPPED vs JUDGED by Wave 4 dedup
-- IF last_reviewed_at were populated (currently all NULLs = always judge).
-- Run after backfill to verify the guard is working.
SELECT
    COUNT(*) FILTER (
        WHERE MAX(tr.reviewed_at) >= NOW() - INTERVAL '30 minutes'
    )                               AS would_be_skipped,
    COUNT(*) FILTER (
        WHERE MAX(tr.reviewed_at) IS NULL
           OR MAX(tr.reviewed_at) < NOW() - INTERVAL '30 minutes'
    )                               AS would_be_judged,
    COUNT(*)                        AS total_active_theses
FROM theses t
LEFT JOIN thesis_reviews tr ON tr.thesis_id = t.id
WHERE t.status = 'active'
GROUP BY ()
;
"""

# ---------------------------------------------------------------------------
# Repair helpers (async, require AsyncSession)
# ---------------------------------------------------------------------------

async def backfill_last_reviewed_at(session: Any) -> dict[str, int]:
    """Backfill Thesis.last_reviewed_at from MAX(ThesisReview.reviewed_at).

    Precondition: migration 0041 has been applied (column exists).
    Safe to run multiple times (idempotent UPDATE).

    Returns:
        {"updated": N, "already_set": M, "no_reviews": K}
    """
    from sqlalchemy import text

    result = await session.execute(text("""
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
          AND (
              t.last_reviewed_at IS NULL
              OR t.last_reviewed_at < sub.max_reviewed_at
          )
        RETURNING t.id
    """))
    updated = result.rowcount

    no_reviews_result = await session.execute(text("""
        SELECT COUNT(*) FROM theses t
        WHERE t.status = 'active'
          AND NOT EXISTS (
              SELECT 1 FROM thesis_reviews tr WHERE tr.thesis_id = t.id
          )
    """))
    no_reviews = no_reviews_result.scalar_one()

    await session.commit()
    return {"updated": updated, "already_set": 0, "no_reviews": no_reviews}


async def run_audit_report(session: Any) -> dict[str, Any]:
    """Run all audit queries and return structured report.

    Non-destructive — read-only queries only.

    Returns:
        {
            "columns_present": list[str],       -- which timestamp cols exist
            "columns_missing": list[str],        -- which are missing
            "stale_theses": list[dict],          -- theses not reviewed in 48h
            "watchlist_thesis_gaps": list[dict], -- tickers with stale thesis in watchlist
            "dedup_effectiveness": dict,         -- skip/judge counts
            "audit_at": str,                     -- ISO timestamp of audit
        }
    """
    from sqlalchemy import text

    report: dict[str, Any] = {"audit_at": datetime.now(UTC).isoformat()}

    # 1. Check which columns exist
    col_check = await session.execute(text(AUDIT_MISSING_LAST_REVIEWED_AT_COL_SQL))
    existing_cols = [row[0] for row in col_check.fetchall()]
    expected_cols = ["last_reviewed_at", "last_judged_at"]
    report["columns_present"] = existing_cols
    report["columns_missing"] = [c for c in expected_cols if c not in existing_cols]

    # 2. Stale theses (>48h without review)
    stale = await session.execute(text(AUDIT_STALE_THESIS_SQL))
    stale_rows = stale.fetchall()
    report["stale_theses"] = [
        {
            "thesis_id": r[0],
            "ticker": r[1],
            "user_id": r[2],
            "status": r[3],
            "created_at": r[4].isoformat() if r[4] else None,
            "updated_at": r[5].isoformat() if r[5] else None,
            "last_reviewed_at": r[6].isoformat() if r[6] else None,
            "hours_since_review": round(float(r[7]), 1) if r[7] else None,
            "review_count": r[8],
        }
        for r in stale_rows
    ]
    report["stale_thesis_count"] = len(stale_rows)

    # 3. Watchlist × thesis gaps
    wl = await session.execute(text(AUDIT_WATCHLIST_WITHOUT_THESIS_SQL))
    wl_rows = wl.fetchall()
    report["watchlist_thesis_gaps"] = [
        {
            "user_id": r[0],
            "ticker": r[1],
            "watchlist_added_at": r[2].isoformat() if r[2] else None,
            "watchlist_updated_at": r[3].isoformat() if r[3] else None,
            "thesis_id": r[4],
            "thesis_last_reviewed_at": r[5].isoformat() if r[5] else None,
        }
        for r in wl_rows
    ]
    report["watchlist_thesis_gap_count"] = len(wl_rows)

    # 4. Dedup effectiveness (only meaningful after backfill)
    dedup = await session.execute(text(AUDIT_DEDUP_GUARD_EFFECTIVENESS_SQL))
    dedup_row = dedup.fetchone()
    if dedup_row:
        report["dedup_effectiveness"] = {
            "would_be_skipped": dedup_row[0],
            "would_be_judged": dedup_row[1],
            "total_active": dedup_row[2],
        }
    else:
        report["dedup_effectiveness"] = {}

    return report
