/**
 * dashboard.js
 * Owner: api/static (read-only UI shell)
 *
 * Responsibilities:
 *   - Fetch data from /api/v1/readmodel/dashboard/{userId}/...
 *   - Render KPIs, theses table, verdict distribution, upcoming catalysts
 *   - Handle reload button & status filter
 *   - No business logic; pure presentation layer
 */

'use strict';

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status} \u2014 ${url}`);
  return res.json();
}

function apiBase(userId) {
  return `/api/v1/readmodel/dashboard/${encodeURIComponent(userId)}`;
}

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

function el(id) {
  return document.getElementById(id);
}

function setText(id, value) {
  el(id).textContent = value ?? '-';
}

function showError(msg) {
  const banner = el('errorBanner');
  banner.textContent = `\u26a0\ufe0f  ${msg}`;
  banner.classList.remove('hidden');
}

function clearError() {
  el('errorBanner').classList.add('hidden');
}

// ---------------------------------------------------------------------------
// Renderers
// ---------------------------------------------------------------------------

function verdictClass(verdict) {
  const map = {
    BULLISH: 'bullish',
    BEARISH: 'bearish',
    NEUTRAL: 'neutral',
    WATCHLIST: 'watchlist',
  };
  return map[String(verdict).toUpperCase()] || '';
}

function statusClass(status) {
  return String(status).toLowerCase();
}

function scoreClass(score) {
  if (score == null) return '';
  if (score >= 70)   return 'score-high';
  if (score >= 40)   return 'score-mid';
  return 'score-low';
}

function renderKpis(stats) {
  setText('openTheses',  stats.open_theses);
  setText('riskyTheses', stats.risky_theses);
  setText('upcoming7d',  stats.upcoming_catalysts_7d);
  setText('reviewsToday', stats.reviews_today);
  setText('totalReviews', stats.total_reviews);
}

function renderVerdicts(stats) {
  const verdictEl = el('verdictList');
  const v = stats?.verdict || {};
  const items = [
    ['BULLISH',   v.BULLISH   || 0],
    ['BEARISH',   v.BEARISH   || 0],
    ['NEUTRAL',   v.NEUTRAL   || 0],
    ['WATCHLIST', v.WATCHLIST || 0],
  ];
  verdictEl.className = 'list';
  verdictEl.innerHTML = items
    .map(
      ([name, count]) => `
        <div class="row">
          <div>${name}</div>
          <span class="badge ${verdictClass(name)}">${count}</span>
        </div>`,
    )
    .join('');
}

function renderCatalysts(items) {
  const catalystEl = el('catalystList');
  if (!items?.length) {
    catalystEl.className = 'empty';
    catalystEl.textContent = 'Kh\u00f4ng c\u00f3 catalyst s\u1eafp t\u1edbi';
    return;
  }
  catalystEl.className = 'list';
  catalystEl.innerHTML = items
    .slice(0, 8)
    .map(
      (item) => `
        <div class="row">
          <div>
            <div><strong>${item.thesis_ticker}</strong> &mdash; ${item.description}</div>
            <div class="muted">${item.expected_date || ''}</div>
          </div>
          <span class="badge ${statusClass(item.thesis_status)}">${item.thesis_status}</span>
        </div>`,
    )
    .join('');
}

function renderTheses(items) {
  const wrap = el('thesesTableWrap');
  if (!items?.length) {
    wrap.className = 'empty';
    wrap.textContent = 'Kh\u00f4ng c\u00f3 thesis ph\u00f9 h\u1ee3p';
    return;
  }
  wrap.className = '';
  const rows = items
    .slice(0, 20)
    .map(
      (t) => `
        <tr>
          <td><strong>${t.ticker}</strong></td>
          <td>${t.title}</td>
          <td><span class="badge ${statusClass(t.status)}">${t.status}</span></td>
          <td class="${scoreClass(t.score)}">${t.score ?? '-'}</td>
          <td>${
            t.last_verdict
              ? `<span class="badge ${verdictClass(t.last_verdict)}">${t.last_verdict}</span>`
              : '<span class="muted">-</span>'
          }</td>
          <td class="muted">${
            t.last_reviewed_at
              ? new Date(t.last_reviewed_at).toLocaleDateString('vi-VN')
              : '-'
          }</td>
        </tr>`,
    )
    .join('');
  wrap.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Ticker</th>
          <th>Title</th>
          <th>Status</th>
          <th>Score</th>
          <th>Last verdict</th>
          <th>Reviewed</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ---------------------------------------------------------------------------
// Load
// ---------------------------------------------------------------------------

async function loadDashboard() {
  const userId = el('userId').value.trim() || 'demo-user';
  const status = el('statusFilter').value || 'active';
  const base   = apiBase(userId);
  clearError();

  const [stats, theses, catalysts] = await Promise.all([
    getJson(`${base}/stats`),
    getJson(`${base}/theses?status=${status}&limit=20`),
    getJson(`${base}/catalysts/upcoming?days=30`),
  ]);

  renderKpis(stats);
  renderVerdicts(stats);
  renderTheses(theses);
  renderCatalysts(catalysts);
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  el('reloadBtn').addEventListener('click', () => {
    loadDashboard().catch((err) => {
      console.error(err);
      showError(err.message);
    });
  });

  el('statusFilter').addEventListener('change', () => {
    loadDashboard().catch((err) => {
      console.error(err);
      showError(err.message);
    });
  });

  loadDashboard().catch((err) => {
    console.error(err);
    showError(err.message);
    el('thesesTableWrap').textContent = 'Load dashboard failed.';
  });
});
