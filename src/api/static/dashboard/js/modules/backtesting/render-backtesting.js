/**
 * render-backtesting.js
 * Owner: modules/backtesting
 */

import { el } from '../../utils/dom.js';
import { badge, esc } from '../../utils/format.js';

function normalizeCount(r) {
  return r.count ?? r.total ?? 0;
}

function normalizeAccuracy(r) {
  if (r.accuracy_pct != null) return Number(r.accuracy_pct).toFixed(1) + '%';
  if (r.accuracy     != null) return (r.accuracy * 100).toFixed(1) + '%';
  if (r.pct          != null) return r.pct + '%';
  return null;
}

// ---------------------------------------------------------------------------
// Verdict distribution
// ---------------------------------------------------------------------------
export function renderVerdicts(list) {
  const wrap = el('verdictList');
  if (!wrap) return;
  if (!list || !list.length) {
    wrap.innerHTML = '<p class="empty-state">Chưa có dữ liệu.</p>';
    return;
  }
  wrap.innerHTML = list.map(v => {
    const count    = normalizeCount(v);
    const accuracy = normalizeAccuracy(v);
    return `
    <div class="row-item">
      <div>
        <div class="row-title">${badge(v.verdict)}</div>
        <div class="row-subtitle">${count} review${accuracy ? ` · ${accuracy} accuracy` : ''}</div>
      </div>
    </div>`;
  }).join('');
}

// ---------------------------------------------------------------------------
// Verdict accuracy table
// ---------------------------------------------------------------------------
export function renderAccuracy(rows) {
  const wrap = el('accuracyWrap');
  if (!wrap) return;
  if (!rows || !rows.length) {
    wrap.innerHTML = '<p class="empty-state">Chưa có dữ liệu accuracy.</p>';
    return;
  }
  wrap.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Verdict</th><th>Count</th><th>Accuracy</th><th>Avg PnL</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map(r => {
          const count    = normalizeCount(r);
          const accuracy = normalizeAccuracy(r);
          const accNum   = r.accuracy_pct != null ? Number(r.accuracy_pct) : null;
          const warn     = accNum != null && accNum < 50;
          const avgPnl   = r.avg_pnl != null ? Number(r.avg_pnl) : null;
          const avgPnlText = avgPnl != null
            ? `<span class="${avgPnl > 0 ? 'score-high' : avgPnl < 0 ? 'score-low' : ''}">${avgPnl > 0 ? '+' : ''}${avgPnl.toFixed(1)}%</span>`
            : '<span class="text-muted" style="font-size:.82rem;">N/A</span>';
          return `
          <tr class="${warn ? 'warn-row' : ''}">
            <td>${badge(r.verdict)}${warn ? ' <span class="warn-badge" title="Accuracy dưới 50%">⚠</span>' : ''}</td>
            <td style="text-align:right">${count}</td>
            <td style="text-align:right">${accuracy ?? '<span class="text-muted" style="font-size:.82rem;">N/A</span>'}</td>
            <td style="text-align:right">${avgPnlText}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;
}

// ---------------------------------------------------------------------------
// Shared table builder
// ---------------------------------------------------------------------------
function buildCallsTable(rows, cols, footerText) {
  const colWidths = { 0: '44px', 2: '68px', 3: '68px', 4: '44px' };
  const colgroup = cols.map((_, i) =>
    colWidths[i] ? `<col style="width:${colWidths[i]}">` : '<col>'
  ).join('');
  const thead = cols.map(c =>
    `<th style="text-align:${c.align ?? 'left'}">${c.label}</th>`
  ).join('');
  const tbody = rows.map(r => {
    const cells = cols.map(c => {
      const raw = c.render ? c.render(r) : esc(String(r[c.key] ?? '—'));
      return `<td style="text-align:${c.align ?? 'left'}"${c.cls ? ` class="${c.cls}"` : ''}>${raw}</td>`;
    }).join('');
    return `<tr>${cells}</tr>`;
  }).join('');
  const footer = footerText ? `<p class="insight-footer">${footerText}</p>` : '';
  return `
    <table>
      <colgroup>${colgroup}</colgroup>
      <thead><tr>${thead}</tr></thead>
      <tbody>${tbody}</tbody>
    </table>${footer}`;
}

const COLS_WORST = [
  { label: 'Mã',       align: 'left',   render: r => `<strong>${esc(r.ticker ?? '—')}</strong>` },
  { label: 'Thesis',   align: 'left',   cls: 'cell-title', render: r => esc(r.title ?? '—') },
  { label: 'Avg PnL',  align: 'right',  render: r => {
      const v = r.avg_pnl_pct ?? r.pnl_pct;
      return v != null ? `<span class="score-low">${Number(v).toFixed(1)}%</span>` : '<span class="text-muted" style="font-size:.82rem;">N/A</span>';
  }},
  { label: 'Drawdown', align: 'right',  render: r => {
      const v = r.min_pnl_pct;
      return v != null ? `<span class="score-low">${Number(v).toFixed(1)}%</span>` : '—';
  }},
  { label: 'Snaps',    align: 'center', render: r => `<span class="muted">${r.snapshot_count ?? '—'}</span>` },
];

const COLS_BEST = [
  { label: 'Mã',      align: 'left',   render: r => `<strong>${esc(r.ticker ?? '—')}</strong>` },
  { label: 'Thesis',  align: 'left',   cls: 'cell-title', render: r => esc(r.title ?? '—') },
  { label: 'Avg PnL', align: 'right',  render: r => {
      const v = r.avg_pnl_pct ?? r.pnl_pct;
      return v != null ? `<span class="score-high">+${Number(v).toFixed(1)}%</span>` : '<span class="text-muted" style="font-size:.82rem;">N/A</span>';
  }},
  { label: 'Peak',    align: 'right',  render: r => {
      const v = r.max_pnl_pct;
      return v != null ? `<span class="score-high">+${Number(v).toFixed(1)}%</span>` : '—';
  }},
  { label: 'Snaps',   align: 'center', render: r => `<span class="muted">${r.snapshot_count ?? '—'}</span>` },
];

export function renderWorstCalls(rows) {
  const wrap = el('worstCallsWrap');
  if (!wrap) return;
  if (!rows || !rows.length) { wrap.innerHTML = '<p class="empty-state">Chưa có dữ liệu.</p>'; return; }
  const footer = (rows._positiveCount ?? 0) > 0 ? `+ ${rows._positiveCount} thesis dương không hiển thị` : '';
  wrap.innerHTML = buildCallsTable(rows, COLS_WORST, footer);
}

export function renderBestCalls(rows) {
  const wrap = el('bestCallsWrap');
  if (!wrap) return;
  if (!rows || !rows.length) { wrap.innerHTML = '<p class="empty-state">Chưa có dữ liệu.</p>'; return; }
  const footer = (rows._negativeCount ?? 0) > 0 ? `+ ${rows._negativeCount} thesis âm không hiển thị` : '';
  wrap.innerHTML = buildCallsTable(rows, COLS_BEST, footer);
}

// ---------------------------------------------------------------------------
// Tab switching — wire once after DOM ready
// ---------------------------------------------------------------------------
export function initCallsTabs() {
  const bar = document.querySelector('.calls-tab-bar');
  if (!bar || bar._wired) return;
  bar._wired = true;
  bar.addEventListener('click', e => {
    const btn = e.target.closest('.calls-tab');
    if (!btn) return;
    const target = btn.dataset.callsTab;
    bar.querySelectorAll('.calls-tab').forEach(b => {
      b.classList.toggle('active', b === btn);
      b.setAttribute('aria-selected', String(b === btn));
    });
    document.getElementById('worstCallsPane')?.classList.toggle('hidden', target !== 'worst');
    document.getElementById('bestCallsPane')?.classList.toggle('hidden',  target !== 'best');
  });
}

// Legacy compat
export function renderPerformance() {
  const wrap = document.getElementById('performanceWrap');
  if (wrap) wrap.style.display = 'none';
}
