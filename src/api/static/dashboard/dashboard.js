'use strict';

function el(id) {
  return document.getElementById(id);
}

function apiBase(userId) {
  return `/api/v1/readmodel/dashboard/${encodeURIComponent(userId)}`;
}

async function getJson(url, options = {}) {
  const res = await fetch(url);
  if (!res.ok) {
    if (options.allow404 && res.status === 404) return null;
    throw new Error(`HTTP ${res.status} — ${url}`);
  }
  return res.json();
}

function setText(id, value) {
  el(id).textContent = value ?? '-';
}

function safeArray(value) {
  return Array.isArray(value) ? value : [];
}

function formatDate(value) {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString('vi-VN');
}

function formatDateShort(value) {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleDateString('vi-VN');
}

function formatNumber(value, digits = 1) {
  if (value == null || value === '') return '-';
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return new Intl.NumberFormat('vi-VN', { maximumFractionDigits: digits }).format(n);
}

function formatPercent(value) {
  if (value == null || value === '') return '-';
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  const sign = n > 0 ? '+' : '';
  return `${sign}${formatNumber(n, 2)}%`;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function showError(msg) {
  const banner = el('errorBanner');
  banner.textContent = `⚠ ${msg}`;
  banner.classList.remove('hidden');
}

function clearError() {
  el('errorBanner').classList.add('hidden');
}

function verdictClass(verdict) {
  const map = {
    BULLISH: 'bullish',
    BEARISH: 'bearish',
    NEUTRAL: 'neutral',
    WATCHLIST: 'watchlist',
  };
  return map[String(verdict || '').toUpperCase()] || '';
}

function statusClass(status) {
  return String(status || '').toLowerCase();
}

function scoreClass(score) {
  if (score == null) return '';
  if (score >= 70) return 'score-high';
  if (score >= 40) return 'score-mid';
  return 'score-low';
}

function pnlClass(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '';
  if (n > 0) return 'pnl-pos';
  if (n < 0) return 'pnl-neg';
  return '';
}

const state = {
  selectedThesisId: null,
  currentTheses: [],
};

function renderKpis(stats) {
  setText('openTheses', stats.open_theses);
  setText('riskyTheses', stats.risky_theses);
  setText('upcoming7d', stats.upcoming_catalysts_7d);
  setText('reviewsToday', stats.reviews_today);
  setText('totalReviewsHero', stats.total_reviews);
  setText('upcoming7dHero', stats.upcoming_catalysts_7d);
}

function renderVerdicts(stats) {
  const verdictEl = el('verdictList');
  const v = stats?.verdict || {};
  const items = [
    ['BULLISH', v.BULLISH || 0],
    ['BEARISH', v.BEARISH || 0],
    ['NEUTRAL', v.NEUTRAL || 0],
    ['WATCHLIST', v.WATCHLIST || 0],
  ];
  verdictEl.className = 'list';
  verdictEl.innerHTML = items.map(([name, count]) => `
    <div class="row-item">
      <div>
        <div class="row-title">${name}</div>
        <div class="row-subtitle">Số review gần nhất theo verdict</div>
      </div>
      <span class="badge ${verdictClass(name)}">${count}</span>
    </div>
  `).join('');
}

function renderCatalysts(items) {
  const catalystEl = el('catalystList');
  if (!items?.length) {
    catalystEl.className = 'empty-state';
    catalystEl.textContent = 'Không có catalyst sắp tới';
    return;
  }
  catalystEl.className = 'list';
  catalystEl.innerHTML = items.slice(0, 8).map((item) => `
    <div class="row-item">
      <div>
        <div class="row-title">${escapeHtml(item.thesis_ticker)} — ${escapeHtml(item.description)}</div>
        <div class="row-subtitle">${formatDate(item.expected_date)}</div>
      </div>
      <div>
        <span class="badge ${statusClass(item.status || 'pending')}">${escapeHtml(item.status || 'pending')}</span>
      </div>
    </div>
  `).join('');
}

function renderTheses(items) {
  state.currentTheses = items || [];
  const wrap = el('thesesTableWrap');
  if (!items?.length) {
    wrap.className = 'empty-state';
    wrap.textContent = 'Không có thesis phù hợp';
    renderThesisDetail(null);
    return;
  }

  const rows = items.slice(0, 20).map((t) => {
    const isSelected = Number(t.id) === Number(state.selectedThesisId);
    return `
      <tr data-thesis-id="${t.id}" class="${isSelected ? 'is-selected' : ''}">
        <td class="ticker-cell">
          <strong>${escapeHtml(t.ticker)}</strong>
          <span>#${t.id}</span>
        </td>
        <td>${escapeHtml(t.title)}</td>
        <td><span class="badge ${statusClass(t.status)}">${escapeHtml(t.status)}</span></td>
        <td class="${scoreClass(t.score)}">${formatNumber(t.score, 1)}</td>
        <td>${t.last_verdict ? `<span class="badge ${verdictClass(t.last_verdict)}">${escapeHtml(t.last_verdict)}</span>` : '<span class="muted">-</span>'}</td>
        <td>${t.n_assumptions ?? 0}</td>
        <td>${t.n_catalysts ?? 0}</td>
        <td class="muted">${formatDateShort(t.last_reviewed_at)}</td>
      </tr>
    `;
  }).join('');

  wrap.className = '';
  wrap.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Ticker</th>
          <th>Title</th>
          <th>Status</th>
          <th>Score</th>
          <th>Last verdict</th>
          <th>Assumptions</th>
          <th>Catalysts</th>
          <th>Reviewed</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;

  wrap.querySelectorAll('tbody tr').forEach((row) => {
    row.addEventListener('click', () => {
      const id = Number(row.dataset.thesisId);
      loadThesisDetail(id).catch((err) => {
        console.error(err);
        showError(err.message);
      });
    });
  });
}

function renderListItems(items, emptyText, formatter) {
  if (!items?.length) return `<div class="muted">${emptyText}</div>`;
  return items.map(formatter).join('');
}

function renderThesisDetail(detail) {
  const wrap = el('thesisDetail');
  if (!detail) {
    wrap.className = 'detail-shell empty-detail';
    wrap.innerHTML = `
      <div class="empty-detail-copy">
        <h3>Chọn một thesis</h3>
        <p>Xem assumptions, catalysts, reasoning và các điểm cần theo dõi tiếp theo.</p>
      </div>
    `;
    return;
  }

  const thesis = detail.thesis || {};
  const assumptions = safeArray(detail.assumptions);
  const catalysts = safeArray(detail.catalysts);
  const reviews = safeArray(detail.reviews);
  const latestReview = reviews[0];

  wrap.className = 'detail-shell';
  wrap.innerHTML = `
    <div class="detail-head">
      <div>
        <p class="eyebrow">Thesis detail</p>
        <h2>${escapeHtml(thesis.ticker || '')} — ${escapeHtml(thesis.title || '')}</h2>
        <div class="detail-meta">
          <span class="badge ${statusClass(thesis.status)}">${escapeHtml(thesis.status || '-')}</span>
          ${latestReview?.verdict ? `<span class="badge ${verdictClass(latestReview.verdict)}">${escapeHtml(latestReview.verdict)}</span>` : ''}
          <span class="badge">Updated ${formatDateShort(thesis.updated_at)}</span>
        </div>
      </div>
    </div>

    <p class="detail-summary">${escapeHtml(thesis.summary || 'Chưa có thesis summary.')}</p>

    <div class="detail-grid">
      <div class="detail-stat">
        <span>Entry / Target</span>
        <strong>${formatNumber(thesis.entry_price, 2)} / ${formatNumber(thesis.target_price, 2)}</strong>
      </div>
      <div class="detail-stat">
        <span>Stop loss / Score</span>
        <strong>${formatNumber(thesis.stop_loss, 2)} / ${formatNumber(thesis.score, 1)}</strong>
      </div>
      <div class="detail-stat">
        <span>Assumptions / Catalysts</span>
        <strong>${assumptions.length} / ${catalysts.length}</strong>
      </div>
    </div>

    <div class="detail-columns">
      <section class="detail-section">
        <h3>Assumptions</h3>
        <div class="detail-list">
          ${renderListItems(assumptions, 'Chưa có assumptions.', (item) => `
            <article class="detail-item">
              <div class="row-title">${escapeHtml(item.description)}</div>
              <span class="badge ${statusClass(item.status)}">${escapeHtml(item.status)}</span>
              ${item.note ? `<p>${escapeHtml(item.note)}</p>` : ''}
            </article>
          `)}
        </div>
      </section>

      <section class="detail-section">
        <h3>Catalysts</h3>
        <div class="detail-list">
          ${renderListItems(catalysts, 'Chưa có catalysts.', (item) => `
            <article class="detail-item">
              <div class="row-title">${escapeHtml(item.description)}</div>
              <span class="badge ${statusClass(item.status)}">${escapeHtml(item.status)}</span>
              <p>Expected: ${formatDate(item.expected_date)}${item.note ? ` · ${escapeHtml(item.note)}` : ''}</p>
            </article>
          `)}
        </div>
      </section>
    </div>

    <section class="review-card">
      <div class="review-head">
        <div>
          <h3>Latest review</h3>
          <div class="review-meta">${latestReview ? `Reviewed ${formatDate(latestReview.reviewed_at)} · Confidence ${formatNumber(latestReview.confidence, 2)}` : 'Chưa có review.'}</div>
        </div>
        ${latestReview?.verdict ? `<span class="badge ${verdictClass(latestReview.verdict)}">${escapeHtml(latestReview.verdict)}</span>` : ''}
      </div>
      <div class="review-reasoning">${escapeHtml(latestReview?.reasoning || 'Chưa có review reasoning.')}</div>
      <div class="review-columns">
        <div class="review-box">
          <strong>Risk signals</strong>
          ${Array.isArray(latestReview?.risk_signals) ? `<ul>${latestReview.risk_signals.map((x) => `<li>${escapeHtml(x)}</li>`).join('')}</ul>` : '<div class="muted">Không có risk signals.</div>'}
        </div>
        <div class="review-box">
          <strong>Next watch items</strong>
          ${Array.isArray(latestReview?.next_watch_items) ? `<ul>${latestReview.next_watch_items.map((x) => `<li>${escapeHtml(x)}</li>`).join('')}</ul>` : '<div class="muted">Không có next watch items.</div>'}
        </div>
      </div>
    </section>
  `;
}

function renderAccuracy(items) {
  const wrap = el('accuracyWrap');
  if (!items?.length) {
    wrap.className = 'empty-state';
    wrap.textContent = 'Chưa có dữ liệu backtesting verdict.';
    return;
  }
  wrap.className = '';
  wrap.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Verdict</th>
          <th>Total</th>
          <th>Hit</th>
          <th>Accuracy</th>
        </tr>
      </thead>
      <tbody>
        ${items.map((item) => `
          <tr>
            <td><span class="badge ${verdictClass(item.verdict)}">${escapeHtml(item.verdict)}</span></td>
            <td>${formatNumber(item.total, 0)}</td>
            <td>${formatNumber(item.hit, 0)}</td>
            <td class="${pnlClass(item.accuracy - 50)}">${formatNumber(item.accuracy, 2)}%</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

function renderPerformances(items) {
  const wrap = el('performanceWrap');
  if (!items?.length) {
    wrap.className = 'empty-state';
    wrap.textContent = 'Chưa có dữ liệu thesis performances.';
    return;
  }
  wrap.className = '';
  wrap.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Ticker</th>
          <th>Snapshots</th>
          <th>Avg PnL</th>
          <th>Max PnL</th>
          <th>Min PnL</th>
        </tr>
      </thead>
      <tbody>
        ${items.map((item) => `
          <tr>
            <td>${escapeHtml(item.ticker || '-')}</td>
            <td>${formatNumber(item.n_snapshots, 0)}</td>
            <td class="${pnlClass(item.avg_pnl_pct)}">${formatPercent(item.avg_pnl_pct)}</td>
            <td class="${pnlClass(item.max_pnl_pct)}">${formatPercent(item.max_pnl_pct)}</td>
            <td class="${pnlClass(item.min_pnl_pct)}">${formatPercent(item.min_pnl_pct)}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

function pickSnapshotTime(obj) {
  return obj?.created_at || obj?.generated_at || obj?.reviewed_at || obj?.updated_at || obj?.snapshot_at || null;
}

function pickSnapshotSummary(obj, fallback) {
  if (!obj) return fallback;
  return obj.summary || obj.content || obj.brief_text || obj.note || obj.status || fallback;
}

function renderSnapshots(scan, morningBrief, eodBrief) {
  setText('latestScanAt', formatDateShort(pickSnapshotTime(scan)));
  setText('latestMorningBriefAt', formatDateShort(pickSnapshotTime(morningBrief)));
  setText('latestEodBriefAt', formatDateShort(pickSnapshotTime(eodBrief)));

  el('latestScanSummary').textContent = pickSnapshotSummary(scan, 'Chưa có scan snapshot.');
  el('latestMorningBriefSummary').textContent = pickSnapshotSummary(morningBrief, 'Chưa có morning brief.');
  el('latestEodBriefSummary').textContent = pickSnapshotSummary(eodBrief, 'Chưa có end-of-day brief.');
}

async function loadThesisDetail(thesisId) {
  const userId = el('userId').value.trim() || 'demo-user';
  state.selectedThesisId = thesisId;
  renderTheses(state.currentTheses);
  const detail = await getJson(`${apiBase(userId)}/theses/${thesisId}`);
  renderThesisDetail(detail);
}

async function loadDashboard() {
  const userId = el('userId').value.trim() || 'demo-user';
  const status = el('statusFilter').value || 'active';
  const base = apiBase(userId);
  clearError();

  const [stats, theses, catalysts, scan, morningBrief, eodBrief, accuracy, performances] = await Promise.all([
    getJson(`${base}/stats`),
    getJson(`${base}/theses?status=${encodeURIComponent(status)}&limit=20`),
    getJson(`${base}/catalysts/upcoming?days=30`),
    getJson(`${base}/scan/latest`, { allow404: true }),
    getJson(`${base}/brief/latest?phase=morning`, { allow404: true }),
    getJson(`${base}/brief/latest?phase=eod`, { allow404: true }),
    getJson(`${base}/backtesting/verdict-accuracy`),
    getJson(`${base}/backtesting/thesis-performances?limit=20`),
  ]);

  renderKpis(stats);
  renderVerdicts(stats);
  renderTheses(theses);
  renderCatalysts(catalysts);
  renderSnapshots(scan, morningBrief, eodBrief);
  renderAccuracy(accuracy);
  renderPerformances(performances);

  const preferredId = state.selectedThesisId || theses?.[0]?.id;
  if (preferredId) {
    await loadThesisDetail(preferredId);
  } else {
    renderThesisDetail(null);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  el('reloadBtn').addEventListener('click', () => {
    loadDashboard().catch((err) => {
      console.error(err);
      showError(err.message);
    });
  });

  el('statusFilter').addEventListener('change', () => {
    state.selectedThesisId = null;
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
