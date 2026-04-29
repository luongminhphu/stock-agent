/**
 * render-backtesting.js
 * Owner: modules/backtesting
 * Responsibility: render Verdict distribution list, Accuracy wrap, Performance wrap.
 * Được tách từ dashboard.js (renderVerdicts, renderAccuracy, renderPerformance).
 */

import { el } from '../../utils/dom.js';
import { badge, esc, fmt, fmtDate } from '../../utils/format.js';

// ---------------------------------------------------------------------------
// Verdict distribution (sidebar)
// ---------------------------------------------------------------------------
export function renderVerdicts(list) {
  const wrap = el('verdictList');
  if (!wrap) return;
  if (!list.length) {
    wrap.innerHTML = '<p class="empty-state">Chưa có dữ liệu.</p>';
    return;
  }
  wrap.innerHTML = list.map(v => `
    <div class="row-item">
      <div>
        <div class="row-title">${badge(v.verdict)}</div>
        <div class="row-subtitle">
          ${v.count ?? v.total ?? 0} review
          · ${v.pct != null
              ? v.pct + '%'
              : v.accuracy != null
                ? (v.accuracy * 100).toFixed(1) + '%'
                : ''}
        </div>
      </div>
    </div>`).join('');
}

// ---------------------------------------------------------------------------
// Verdict accuracy table (backtesting section)
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
          <th>Verdict</th>
          <th>Count</th>
          <th>Accuracy</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map(r => `
          <tr>
            <td>${badge(r.verdict)}</td>
            <td>${r.count ?? r.total ?? '—'}</td>
            <td>${r.accuracy != null ? (r.accuracy * 100).toFixed(1) + '%' : r.pct != null ? r.pct + '%' : '—'}</td>
          </tr>`).join('')}
      </tbody>
    </table>`;
}

// ---------------------------------------------------------------------------
// Thesis performance table (backtesting section)
// ---------------------------------------------------------------------------
export function renderPerformance(rows) {
  const wrap = el('performanceWrap');
  if (!wrap) return;
  if (!rows || !rows.length) {
    wrap.innerHTML = '<p class="empty-state">Chưa có dữ liệu performance.</p>';
    return;
  }
  wrap.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Mã</th>
          <th>Thesis</th>
          <th>PnL</th>
          <th>Reviews</th>
          <th>Cập nhật</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map(r => `
          <tr>
            <td><strong>${esc(r.ticker ?? '—')}</strong></td>
            <td style="max-width:200px;">${esc(r.title ?? '—')}</td>
            <td class="${r.pnl_pct > 0 ? 'score-high' : r.pnl_pct < 0 ? 'score-low' : ''}">
              ${r.pnl_pct != null ? (r.pnl_pct > 0 ? '+' : '') + r.pnl_pct.toFixed(1) + '%' : '—'}
            </td>
            <td>${r.review_count ?? '—'}</td>
            <td style="color:var(--muted);font-size:.82rem;">${fmtDate(r.updated_at)}</td>
          </tr>`).join('')}
      </tbody>
    </table>`;
}
