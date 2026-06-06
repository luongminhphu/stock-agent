/**
 * leaderboard-service.js — Wave A: Thesis Leaderboard Strip
 * Owner  : readmodel (query) + dashboard (static adapter)
 * API    : GET /api/v1/readmodel/leaderboard?sort_by=score|pnl&limit=5
 * HTML   : #leaderboardStrip  →  ol#leaderboardList
 * CSS    : css/modules/leaderboard.css
 *
 * LeaderboardEntry fields (from readmodel/schemas.py):
 *   rank, thesis_id, ticker, title, score, pnl_pct,
 *   last_verdict, status, created_at
 *
 * Exports:
 *   loadLeaderboard(sortBy?)  — fetch + render, wires sort buttons on first call
 */

import { getJson } from '../../api/client.js';
import { loadThesisDetail } from '../thesis/thesis-service.js';

const LIMIT        = 5;
let   _wired       = false;
let   _itemsWired  = false;
let   _current     = 'score';

// ---------------------------------------------------------------------------
// Public
// ---------------------------------------------------------------------------

export async function loadLeaderboard(sortBy = 'score') {
  _current = sortBy;
  const strip = document.getElementById('leaderboardStrip');
  const list  = document.getElementById('leaderboardList');
  if (!strip || !list) return;

  strip.setAttribute('aria-busy', 'true');
  _updateSortButtons(sortBy);

  if (!_wired) {
    _wireSortButtons();
    _wired = true;
  }

  try {
    // LeaderboardResponse shape: { user_id, sort_by, entries: [...] }
    const data  = await getJson(
      `/api/v1/readmodel/leaderboard?sort_by=${sortBy}&limit=${LIMIT}`
    );
    const items = Array.isArray(data) ? data : (data.entries ?? []);
    _render(list, items, sortBy);

    if (!_itemsWired) {
      _wireItemClicks(list);
      _itemsWired = true;
    }
  } catch (err) {
    list.innerHTML = `<li class="lb-empty">Không tải được leaderboard: ${err.message}</li>`;
  } finally {
    strip.setAttribute('aria-busy', 'false');
  }
}

// ---------------------------------------------------------------------------
// Private: render list items
// ---------------------------------------------------------------------------

// Compute tier client-side from score (0–100)
const TIER_LABEL = score =>
  score == null  ? ''           :
  score >= 80    ? '🔥 Strong'  :
  score >= 60    ? '✅ Good'    :
  score >= 40    ? '⚠️ Watch'   : '🔴 Risky';

// Backend only supports sort_by: "score" | "pnl"
function _metricDisplay(item, sortBy) {
  if (sortBy === 'pnl') {
    const pnl = item.pnl_pct;
    return {
      value : pnl != null ? `${pnl > 0 ? '+' : ''}${pnl.toFixed(1)}%` : '—',
      label : 'P&L',
      cls   : pnl == null ? '' : pnl > 0 ? 'up' : 'down',
    };
  }
  // default: score
  return {
    value : item.score != null ? item.score : '—',
    label : 'Score',
    cls   : '',
  };
}

// Tier badge HTML — compact, no emoji text noise
const TIER_BADGE = score =>
  score == null  ? '' :
  score >= 80    ? '<span class="lb-tier lb-tier--a">Strong</span>'  :
  score >= 60    ? '<span class="lb-tier lb-tier--b">Good</span>'    :
  score >= 40    ? '<span class="lb-tier lb-tier--c">Watch</span>'   :
                   '<span class="lb-tier lb-tier--d">Risky</span>';

const VERDICT_CLS = v => {
  const s = String(v ?? '').toLowerCase();
  return s === 'bullish' || s === 'buy'  ? 'lb-vd--bull' :
         s === 'bearish' || s === 'sell' ? 'lb-vd--bear' :
         s === 'neutral' || s === 'hold' ? 'lb-vd--neut' : 'lb-vd--neut';
};

function _render(listEl, items, sortBy) {
  if (!items.length) {
    listEl.innerHTML = '<li class="lb-empty">Chưa có thesis nào.</li>';
    return;
  }

  listEl.innerHTML = items.map((item, idx) => {
    const rank    = item.rank ?? (idx + 1);
    const metric  = _metricDisplay(item, sortBy);
    const tid     = item.thesis_id ?? '';

    // Metric value with colour
    const valCls  = metric.cls === 'up' ? 'lb-val--pos'
                  : metric.cls === 'down' ? 'lb-val--neg' : '';

    // Verdict badge — only when present
    const verdictHtml = item.last_verdict
      ? `<span class="lb-vd ${VERDICT_CLS(item.last_verdict)}">${_esc(item.last_verdict.toUpperCase())}</span>`
      : '';

    // Tier badge — only for score sort
    const tierHtml = sortBy === 'score' ? TIER_BADGE(item.score) : '';

    return `<li class="lb-item"
        role="button" tabindex="0"
        data-thesis-id="${_esc(String(tid))}"
        aria-label="${_esc(item.ticker ?? '')} — ${_esc(item.title ?? '')}">
      <span class="lb-rank">${rank}</span>
      <span class="lb-ticker">${_esc(item.ticker ?? '—')}</span>
      <span class="lb-name">${_esc(item.title ?? '')}</span>
      <span class="lb-badges">${verdictHtml}${tierHtml}</span>
      <span class="lb-val ${valCls}">${_esc(String(metric.value))}</span>
    </li>`;
  }).join('');
}

// ---------------------------------------------------------------------------
// Private: wire item clicks → thesis detail
// ---------------------------------------------------------------------------

function _wireItemClicks(listEl) {
  // Event delegation — handles re-renders without re-wiring
  listEl.addEventListener('click', e => {
    const item = e.target.closest('.lb-item[data-thesis-id]');
    if (!item) return;
    _openDetail(item.dataset.thesisId);
  });

  listEl.addEventListener('keydown', e => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const item = e.target.closest('.lb-item[data-thesis-id]');
    if (!item) return;
    e.preventDefault();
    _openDetail(item.dataset.thesisId);
  });
}

async function _openDetail(thesisId) {
  if (!thesisId) return;

  // Load detail (thesis-service handles render into #thesisDetail)
  await loadThesisDetail(thesisId);

  // Scroll thesis detail into view
  const detail = document.getElementById('thesisDetail');
  if (detail) {
    detail.scrollIntoView({ behavior: 'smooth', block: 'start' });
    // Brief highlight flash to orient the user
    detail.classList.add('lb-detail-flash');
    setTimeout(() => detail.classList.remove('lb-detail-flash'), 900);
  }
}

// ---------------------------------------------------------------------------
// Private: sort button wiring + active state
// Sort options: score | pnl  (conviction removed — not in LeaderboardEntry)
// ---------------------------------------------------------------------------

function _wireSortButtons() {
  const strip = document.getElementById('leaderboardStrip');
  strip?.querySelector('.lb-sort-bar')?.addEventListener('click', e => {
    const btn = e.target.closest('.lb-sort-btn');
    if (!btn || btn.dataset.sort === _current) return;
    const sort = btn.dataset.sort;
    // Guard: only allow valid backend values
    if (sort !== 'score' && sort !== 'pnl') return;
    loadLeaderboard(sort);
  });
}

function _updateSortButtons(sortBy) {
  document.querySelectorAll('.lb-sort-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.sort === sortBy);
  });
}

// ---------------------------------------------------------------------------
// Private: minimal XSS escape
// ---------------------------------------------------------------------------

function _esc(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
