/**
 * render-backtesting.js
 * Owner: modules/backtesting
 * Responsibility: render Verdict distribution list, Accuracy wrap,
 *                 Worst calls wrap, Best calls wrap.
 */

import { el } from '../../utils/dom.js';
import { badge, esc, fmt, fmtDate } from '../../utils/format.js';

// ---------------------------------------------------------------------------
// Normalize helpers
// ---------------------------------------------------------------------------
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
// Verdict accuracy table — thêm avg_pnl col + warning khi accuracy < 50%
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
          <th>Avg PnL</th>
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
            <td>${badge(r.verdict)}${warn ? ' <span class="warn-badge" title="Accuracy dưới 50% — cần review ngưỡng">⚠</span>' : ''}</td>
            <td>${count}</td>
            <td title="${accuracy ? '' : 'Backend chưa tính accuracy cho verdict này'}">
              ${accuracy ?? '<span class="text-muted" style="font-size:.82rem;">N/A</span>'}
            </td>
            <td>${avgPnlText}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;
}

// ---------------------------------------------------------------------------
// Worst calls — top 5 thesis có avg_pnl_pct thấp nhất (âm)
// ---------------------------------------------------------------------------
export function renderWorstCalls(rows) {
  const wrap = el('worstCallsWrap');
  if (!wrap) return;
  if (!rows || !rows.length) {
    wrap.innerHTML = '<p class="empty-state">Chưa có dữ liệu.</p>';
    return;
  }

  // Caller đã sort + slice; ta chỉ render
  const positiveCount = rows._positiveCount ?? 0;

  wrap.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Mã</th>
          <th>Thesis</th>
          <th>Avg PnL</th>
          <th>Drawdown</th>
          <th>Snaps</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map(r => {
          const pnl  = r.avg_pnl_pct ?? r.pnl_pct ?? null;
          const draw = r.min_pnl_pct ?? null;
          const pnlText  = pnl  != null ? `<span class="score-low">${Number(pnl).toFixed(1)}%</span>`  : '<span class="text-muted" style="font-size:.82rem;">N/A</span>';
          const drawText = draw != null ? `<span class="score-low">${Number(draw).toFixed(1)}%</span>` : '—';
          return `
          <tr>
            <td><strong>${esc(r.ticker ?? '—')}</strong></td>
            <td class="cell-title">${esc(r.title ?? '—')}</td>
            <td>${pnlText}</td>
            <td>${drawText}</td>
            <td class="cell-center muted">${r.snapshot_count ?? '—'}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>
    ${positiveCount > 0 ? `<p class="insight-footer">+ ${positiveCount} thesis dương không hiển thị</p>` : ''}`;
}

// ---------------------------------------------------------------------------
// Best calls — top 5 thesis có avg_pnl_pct cao nhất (dương)
// ---------------------------------------------------------------------------
export function renderBestCalls(rows) {
  const wrap = el('bestCallsWrap');
  if (!wrap) return;
  if (!rows || !rows.length) {
    wrap.innerHTML = '<p class="empty-state">Chưa có dữ liệu.</p>';
    return;
  }

  const negativeCount = rows._negativeCount ?? 0;

  wrap.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Mã</th>
          <th>Thesis</th>
          <th>Avg PnL</th>
          <th>Peak</th>
          <th>Snaps</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map(r => {
          const pnl  = r.avg_pnl_pct ?? r.pnl_pct ?? null;
          const peak = r.max_pnl_pct ?? null;
          const pnlText  = pnl  != null ? `<span class="score-high">+${Number(pnl).toFixed(1)}%</span>`  : '<span class="text-muted" style="font-size:.82rem;">N/A</span>';
          const peakText = peak != null ? `<span class="score-high">+${Number(peak).toFixed(1)}%</span>` : '—';
          return `
          <tr>
            <td><strong>${esc(r.ticker ?? '—')}</strong></td>
            <td class="cell-title">${esc(r.title ?? '—')}</td>
            <td>${pnlText}</td>
            <td>${peakText}</td>
            <td class="cell-center muted">${r.snapshot_count ?? '—'}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>
    ${negativeCount > 0 ? `<p class="insight-footer">+ ${negativeCount} thesis âm không hiển thị</p>` : ''}`;
}

// ---------------------------------------------------------------------------
// Legacy export — giữ backward compat nếu code cũ còn import renderPerformance
// ---------------------------------------------------------------------------
export function renderPerformance(rows) {
  // Delegate sang renderWorstCalls để không break import cũ;
  // caller mới nên dùng renderWorstCalls / renderBestCalls trực tiếp.
  const wrap = el('performanceWrap');
  if (wrap) wrap.style.display = 'none'; // ẩn nếu DOM cũ còn tồn tại
}
