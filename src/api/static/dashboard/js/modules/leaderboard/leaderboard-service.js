/**
 * leaderboard-service.js — Wave A: Thesis Leaderboard Strip
 * Owner  : readmodel (query) + dashboard (static adapter)
 * API    : GET /api/v1/readmodel/leaderboard?sort_by=score&limit=5
 * HTML   : #leaderboardStrip  →  ol#leaderboardList
 * CSS    : css/modules/leaderboard.css
 *
 * Exports:
 *   loadLeaderboard(sortBy?)  — fetch + render, wires sort buttons on first call
 */

import { getJson } from '../../api/client.js';

const LIMIT    = 5;
let   _wired   = false;
let   _current = 'score';

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
    const data  = await getJson(
      `/api/v1/readmodel/leaderboard?sort_by=${sortBy}&limit=${LIMIT}`
    );
    const items = Array.isArray(data) ? data : (data.items ?? []);
    _render(list, items, sortBy);
  } catch (err) {
    list.innerHTML = `<li class="lb-empty">Không tải được leaderboard: ${err.message}</li>`;
  } finally {
    strip.setAttribute('aria-busy', 'false');
  }
}

// ---------------------------------------------------------------------------
// Private: render list items
// ---------------------------------------------------------------------------

const TIER_LABEL = score =>
  score >= 80 ? '🔥 Strong' :
  score >= 60 ? '✅ Good'   :
  score >= 40 ? '⚠️ Watch'  : '🔴 Risky';

function _metricDisplay(item, sortBy) {
  switch (sortBy) {
    case 'conviction':
      return {
        value : item.conviction_score != null ? item.conviction_score : '—',
        label : 'Conviction',
        cls   : '',
      };
    case 'pnl': {
      const pnl = item.pnl_pct;
      return {
        value : pnl != null ? `${pnl > 0 ? '+' : ''}${pnl.toFixed(1)}%` : '—',
        label : 'P&L',
        cls   : pnl == null ? '' : pnl > 0 ? 'up' : 'down',
      };
    }
    default: // score
      return {
        value : item.score != null ? item.score : '—',
        label : 'Score',
        cls   : '',
      };
  }
}

function _render(listEl, items, sortBy) {
  if (!items.length) {
    listEl.innerHTML = '<li class="lb-empty">Chưa có thesis nào trong leaderboard.</li>';
    return;
  }

  listEl.innerHTML = items.map((item, idx) => {
    const rank   = idx + 1;
    const metric = _metricDisplay(item, sortBy);
    const tier   = item.score != null ? TIER_LABEL(item.score) : '';
    return `
      <li class="lb-item" role="listitem">
        <span class="lb-rank" data-rank="${rank}">#${rank}</span>
        <div class="lb-ticker-row">
          <span class="lb-ticker">${_esc(item.ticker ?? '—')}</span>
          <span class="lb-title">${_esc(item.title ?? '')}</span>
        </div>
        <div class="lb-metric ${metric.cls}">
          ${_esc(String(metric.value))}
          <span class="lb-metric-label">${metric.label}</span>
          <span class="lb-tier">${tier}</span>
        </div>
      </li>
    `.trim();
  }).join('');
}

// ---------------------------------------------------------------------------
// Private: sort button wiring + active state
// ---------------------------------------------------------------------------

function _wireSortButtons() {
  const strip = document.getElementById('leaderboardStrip');
  strip?.querySelector('.lb-sort-bar')?.addEventListener('click', e => {
    const btn = e.target.closest('.lb-sort-btn');
    if (!btn || btn.dataset.sort === _current) return;
    loadLeaderboard(btn.dataset.sort);
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
