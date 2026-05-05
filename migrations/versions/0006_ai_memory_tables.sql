-- Migration: 0006_ai_memory_tables
-- Description: Add Layer 2 (ai_interaction_logs) and Layer 3 (memory_snapshots)
--              for the ai.memory 3-layer memory system (Blueprint V2).
-- Depends on: 0005 (or whatever the current latest migration is)
-- Run: psql $DATABASE_URL -f migrations/versions/0006_ai_memory_tables.sql

-- ---------------------------------------------------------------------------
-- Layer 2 — Episodic Memory
-- One row per AI agent call. Written by MemoryService.log_interaction().
-- Read by Consolidator (weekly) and ContextBuilder (per-call, last 15 rows).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ai_interaction_logs (
    id              SERIAL PRIMARY KEY,
    user_id         VARCHAR(64)  NOT NULL,
    agent_type      VARCHAR(64)  NOT NULL,   -- briefing | pretrade | replay | watchdog | thesis_review | suggest
    trigger         VARCHAR(128) NOT NULL DEFAULT 'unknown',
    tickers_json    TEXT,                    -- JSON list, e.g. '["VCB","VNM"]'
    ai_verdict      VARCHAR(32),             -- BULLISH | BEARISH | NEUTRAL | GO | NO_GO | HOLD
    ai_confidence   FLOAT,
    ai_key_points   TEXT,                    -- newline-separated prose (max 5 lines)
    ai_risk_signals TEXT,                    -- newline-separated prose (max 5 lines)
    thesis_id       INTEGER,
    decision_id     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_ai_interaction_logs_user_id
    ON ai_interaction_logs (user_id);

CREATE INDEX IF NOT EXISTS ix_ai_interaction_logs_created_at
    ON ai_interaction_logs (created_at);

CREATE INDEX IF NOT EXISTS ix_ai_interaction_logs_user_created
    ON ai_interaction_logs (user_id, created_at DESC);

COMMENT ON TABLE ai_interaction_logs IS
    'Layer 2 Episodic Memory — one row per AI agent call. Owner: ai segment.';

-- ---------------------------------------------------------------------------
-- Layer 3 — Semantic Memory
-- One row per weekly consolidation per user. Written by MemoryConsolidator.
-- Read by ContextBuilder._fetch_memory_context() (latest row only).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS memory_snapshots (
    id                      SERIAL PRIMARY KEY,
    user_id                 VARCHAR(64)  NOT NULL,
    period_start            TIMESTAMPTZ  NOT NULL,
    period_end              TIMESTAMPTZ  NOT NULL,
    behavioral_patterns     TEXT,
    cognitive_biases        TEXT,
    strengths               TEXT,
    blind_spots             TEXT,
    confidence_calibration  TEXT,
    episode_count           INTEGER NOT NULL DEFAULT 0,
    verdict_accuracy        FLOAT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_memory_snapshots_user_id
    ON memory_snapshots (user_id);

CREATE INDEX IF NOT EXISTS ix_memory_snapshots_created_at
    ON memory_snapshots (created_at);

CREATE INDEX IF NOT EXISTS ix_memory_snapshots_user_created
    ON memory_snapshots (user_id, created_at DESC);

COMMENT ON TABLE memory_snapshots IS
    'Layer 3 Semantic Memory — weekly AI-distilled investor snapshot. Owner: ai segment.';
