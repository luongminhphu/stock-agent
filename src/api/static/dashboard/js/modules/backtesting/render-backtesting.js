/**
 * render-backtesting.js
 * Owner: modules/backtesting
 * Responsibility: render Verdict distribution list, Accuracy wrap, Performance wrap.
 * Được tách từ dashboard.js (renderVerdicts, renderAccuracy, renderPerformance).
 */

import { el } from '../../utils/dom.js';
import { badge, esc, fmt, fmtDate } from '../../utils/format.js';

// ---------------------------------------------------------------------------
// Normalize helpers — chuẩn hóa field names từ API (count/total, accuracy/pct)
// ---------------------------------------------------------------------------
function normalizeCount(r) {
  return r.count ?? r.total ?? 0;
}

function normalizeAccuracy(r) {
  if (r.accuracy != null) return (r.accuracy * 100).toFixed(1) + '%';
  if (r.pct      != null) return r.pct + '%';
  return null; // chưa có data
}

// ---------------------------------------------------------------------------
// Verdict distribution (sidebar)
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
        <div class="row-subtitle">
          ${count} review${accuracy ? ` · ${accuracy} accuracy` : ''}
        </div>
      </div>
    </div>`;
  }).join('');
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
        ${rows.map(r => {
          const count    = normalizeCount(r);
          const accuracy = normalizeAccuracy(r);
          return `
          <tr>
            <td>${badge(r.verdict)}</td>
            <td>${count}</td>
            <td title="${accuracy ? '' : 'Backend chưa tính accuracy cho verdict này'}">
              ${accuracy ?? '<span class="text-muted" style="font-size:.82rem;">N/A</span>'}
            </td>
          </tr>`;
        }).join('')}
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
        ${rows.map(r => {
          const pnl     = r.pnl_pct ?? r.pnl ?? null;
          const pnlText = pnl != null
            ? `${pnl > 0 ? '+' : ''}${Number(pnl).toFixed(1)}%`
            : '<span class="text-muted" style="font-size:.82rem;" title="Chưa có dữ liệu giá">N/A</span>';
          const pnlClass = pnl != null
            ? (pnl > 0 ? 'score-high' : pnl < 0 ? 'score-low' : '')
            : '';
          return `
          <tr>
            <td><strong>${esc(r.ticker ?? '—')}</strong></td>
            <td style="max-width:200px;">${esc(r.title ?? '—')}</td>
            <td class="${pnlClass}">${pnlText}</td>
            <td>${r.review_count ?? r.reviews ?? '—'}</td>
            <td style="color:var(--muted);font-size:.82rem;">${fmtDate(r.updated_at)}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;
}
