/**
 * today-loop-loader.js — Daily investor intelligence loop
 * Segment owner: api (thin adapter) / readmodel (data)
 * Endpoint: GET /api/v1/today-loop
 *
 * Aggregates 5 sources in ONE fetch:
 *   attention_items  → ignored (loadAttentionPanel() handles this independently)
 *   top_signals      → badge count on #signalsFeed header
 *   thesis_digest    → renders #thesisDigestStrip (low_conviction + overdue)
 *   market_mood      → updates #latestScanCard KPI
 *   meta             → updates badge counts
 *
 * Design: today-loop is ADDITIVE — does not replace loadAttentionPanel().
 * It enriches the dashboard with data that loadDashboard() doesn't cover yet:
 *   thesis_digest and top_signals ranked by strength from today's perspective.
 *
 * Auto-refresh: mỗi 10 phút (heavier than attention — has AI-derived mood).
 */

import {
  renderThesisDigest,
  updateMarketMoodKpi,
  updateSignalsBadge,
} from './today-loop-renderer.js';

const TODAY_LOOP_URL = '/api/v1/today-loop';
const REFRESH_INTERVAL_MS = 10 * 60 * 1000;
let _refreshTimer = null;

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

async function fetchTodayLoop({ attentionLimit = 5, signalLimit = 10 } = {}) {
  const url = `${TODAY_LOOP_URL}?attention_limit=${attentionLimit}&signal_limit=${signalLimit}`;
  const res = await fetch(url, { headers: { 'Content-Type': 'application/json' } });
  if (!res.ok) throw new Error(`today-loop ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Distribute — only the 3 new targets; attention stays with attention-loader
// ---------------------------------------------------------------------------

function distributeToUI(data) {
  const {
    top_signals    = [],
    thesis_digest  = [],
    market_mood    = {},
    stale_sources  = [],
    generated_at,
  } = data;

  updateSignalsBadge(top_signals);
  renderThesisDigest(thesis_digest, { generatedAt: generated_at });
  updateMarketMoodKpi(market_mood, {
    stale: stale_sources.includes('scan_snapshot'),
  });

  if (stale_sources.length) {
    console.warn('[today-loop] stale sources:', stale_sources.join(', '));
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function loadTodayLoop({ silent = false } = {}) {
  try {
    const data = await fetchTodayLoop();
    distributeToUI(data);
  } catch (err) {
    if (!silent) console.warn('[today-loop] fetch failed:', err.message);
  }
}

export function startTodayLoopAutoRefresh() {
  if (_refreshTimer) clearInterval(_refreshTimer);
  _refreshTimer = setInterval(() => loadTodayLoop({ silent: true }), REFRESH_INTERVAL_MS);
}
