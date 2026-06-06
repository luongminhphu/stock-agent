/**
 * render-backtesting.js
 * Owner: modules/backtesting
 *
 * Compact redesign:
 *   renderAccuracy(rows)    → #accuracyWrap  (verdict · count · accuracy · avg PnL)
 *   renderWorstCalls(rows)  → #worstCallsWrap
 *   renderBestCalls(rows)   → #bestCallsWrap
 *   initCallsTabs()         → wire .calls-tab-bar
 *   normalizeAccuracyRes()  → re-exported for dashboard-loader compat
 *
 * renderVerdicts() removed — #verdictList does not exist in HTML.
 */

import { el } from '../../utils/dom.js';
import { esc } from '../../utils/format.js';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function _fmtPct(v, opts = {}) {
  if (v == null) return '<span class="bt-na">—</span>';
  const n = Number(v);
  const s = opts.sign && n > 0 ? '+' : '';
  const cls = opts.color
    ? (n > 0 ? 'bt-pos' : n < 0 ? 'bt-neg' : 'bt-neu')
    : '';
  return cls
    ? `<span class="${cls}">${s}${n.toFixed(1)}%</span>`
    : `${s}${n.toFixed(1)}%`;
}

function _fmtCount(v) {
  return v == null ? '<span class="bt-na">—</span>' : String(v);
}

function _verdictBadge(v) {
  if (!v) return '<span class="bt-na">—</span>';
  const s = String(v).toLowerCase();
  const cls = s === 'bullish' || s === 'buy'  ? 'bt-vd--bull'
            : s === 'bearish' || s === 'sell' ? 'bt-vd--bear'
            : 'bt-vd--neut';
  return `<span class="bt-vd ${cls}">${esc(v.toUpperCase())}</span>`;
}

// ---------------------------------------------------------------------------
// Accuracy table
// ---------------------------------------------------------------------------

export function renderAccuracy(rows) {
  const wrap = el('accuracyWrap');
  if (!wrap) return;

  if (!rows || !rows.length) {
    wrap.innerHTML = '<p class="bt-empty">Chưa có dữ liệu.</p>';
    return;
  }

  const body = rows.map(r => {
    const count    = r.count ?? r.total ?? 0;
    const accNum   = r.accuracy_pct != null ? Number(r.accuracy_pct)
                   : r.accuracy     != null ? Number(r.accuracy) * 100 : null;
    const accHtml  = accNum != null
      ? `<span class="${accNum >= 50 ? 'bt-pos' : 'bt-neg'}">${accNum.toFixed(0)}%</span>`
      : '<span class="bt-na">—</span>';
    const warn     = accNum != null && accNum < 50
      ? ' <span class="bt-warn" title="Accuracy &lt; 50%">⚠</span>' : '';

    return `<tr>
      <td class="col-left">${_verdictBadge(r.verdict)}${warn}</td>
      <td class="num">${_fmtCount(count)}</td>
      <td class="num">${accHtml}</td>
      <td class="num">${_fmtPct(r.avg_pnl, { sign: true, color: true })}</td>
    </tr>`;
  }).join('');

  wrap.innerHTML = `
    <table class="bt-table">
      <thead><tr>
        <th class="col-left">Verdict</th>
        <th class="num">Count</th>
        <th class="num">Accuracy</th>
        <th class="num">Avg PnL</th>
      </tr></thead>
      <tbody>${body}</tbody>
    </table>`;
}

// ---------------------------------------------------------------------------
// Calls tables (Worst / Best)
// ---------------------------------------------------------------------------

function _buildCallsTable(rows, type) {
  const isWorst = type === 'worst';
  const pnlKey  = 'avg_pnl_pct';
  const extKey  = isWorst ? 'min_pnl_pct' : 'max_pnl_pct';
  const extLbl  = isWorst ? 'DD' : 'Peak';

  const body = rows.map(r => {
    const pnl  = _fmtPct(r[pnlKey], { sign: true, color: true });
    const ext  = _fmtPct(r[extKey], { sign: true, color: true });
    const snaps = r.snapshot_count != null
      ? `<span class="bt-na">${r.snapshot_count}x</span>` : '<span class="bt-na">—</span>';

    return `<tr>
      <td class="col-left bt-ticker">${esc(r.ticker ?? '—')}</td>
      <td class="bt-title">${esc(r.title ?? '—')}</td>
      <td class="num">${pnl}</td>
      <td class="num">${ext}</td>
      <td class="num">${snaps}</td>
    </tr>`;
  }).join('');

  return `
    <table class="bt-table">
      <thead><tr>
        <th class="col-left">Mã</th>
        <th>Thesis</th>
        <th class="num">Avg PnL</th>
        <th class="num">${extLbl}</th>
        <th class="num">Snaps</th>
      </tr></thead>
      <tbody>${body}</tbody>
    </table>`;
}

export function renderWorstCalls(rows) {
  const wrap = el('worstCallsWrap');
  if (!wrap) return;
  if (!rows || !rows.length) { wrap.innerHTML = '<p class="bt-empty">Chưa có dữ liệu.</p>'; return; }
  const extra = (rows._positiveCount ?? 0) > 0
    ? `<p class="bt-footer">+${rows._positiveCount} thesis dương không hiển thị</p>` : '';
  wrap.innerHTML = _buildCallsTable(rows, 'worst') + extra;
}

export function renderBestCalls(rows) {
  const wrap = el('bestCallsWrap');
  if (!wrap) return;
  if (!rows || !rows.length) { wrap.innerHTML = '<p class="bt-empty">Chưa có dữ liệu.</p>'; return; }
  const extra = (rows._negativeCount ?? 0) > 0
    ? `<p class="bt-footer">+${rows._negativeCount} thesis âm không hiển thị</p>` : '';
  wrap.innerHTML = _buildCallsTable(rows, 'best') + extra;
}

// ---------------------------------------------------------------------------
// Tab switching
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

// ---------------------------------------------------------------------------
// Compat stubs — kept so dashboard-loader.js imports don't break
// ---------------------------------------------------------------------------

/** @deprecated No #verdictList in HTML — noop */
export function renderVerdicts() {}

/** @deprecated */
export function renderPerformance() {
  const wrap = document.getElementById('performanceWrap');
  if (wrap) wrap.style.display = 'none';
}
