/**
 * modules/market/breadth.js
 * Owner: market segment (data) / dashboard adapter (render)
 *
 * Fetches GET /api/v1/market/breadth for HOSE, HNX, UPCOM in parallel
 * and renders 3 stacked rows into #marketBreadthBar.
 *
 * Graceful degradation:
 *   - 503 (adapter not configured): silently hidden — not an error.
 *   - 502 / network error: hidden with console.warn.
 *   - All 3 exchanges return empty: container hidden.
 */

import { marketApiBase, getJson } from '../../api/client.js?v=1';

const ELEMENT_ID  = 'marketBreadthBar';
const EXCHANGES   = ['HOSE', 'HNX', 'UPCOM'];

/**
 * Fetch breadth for all 3 exchanges in parallel and render stacked rows.
 */
export async function loadMarketBreadth() {
  const wrap = document.getElementById(ELEMENT_ID);
  if (!wrap) return;

  const results = await Promise.allSettled(
    EXCHANGES.map(ex => getJson(`${marketApiBase()}/breadth?exchange=${ex}`))
  );

  // Collect successful, non-empty responses
  const rows = [];
  let allSilent = true;
  for (let i = 0; i < EXCHANGES.length; i++) {
    const r = results[i];
    if (r.status === 'fulfilled' && r.value && r.value.total > 0) {
      rows.push({ exchange: EXCHANGES[i], data: r.value });
      allSilent = false;
    } else if (r.status === 'rejected') {
      const msg = r.reason?.message ?? '';
      if (!msg.startsWith('503')) {
        console.warn(`[breadth] ${EXCHANGES[i]} failed:`, msg);
      }
    }
  }

  if (allSilent || rows.length === 0) {
    wrap.classList.add('hidden');
    return;
  }

  wrap.classList.remove('hidden');
  wrap.innerHTML = rows
    .map((row, idx) => renderBreadthRow(row.data, row.exchange, idx < rows.length - 1))
    .join('');
}

/**
 * Render a single exchange breadth row as an HTML string.
 * @param {object} d          BreadthResponse shape
 * @param {string} exchange
 * @param {boolean} divider   render bottom divider (all rows except last)
 * @returns {string}
 */
function renderBreadthRow(d, exchange, divider = false) {
  const advPct  = d.advance_pct   ?? 0;
  const decPct  = d.decline_pct   ?? 0;
  const unchPct = d.unchanged_pct ?? 0;

  // UI Wave U1: sentiment = chữ + class màu (không emoji). Màu theo token
  // giá HSC (--up-rgb / --down-rgb / --ref-rgb) để khớp bảng giá.
  const [sentiment, sentimentCls] =
    advPct >= 60 ? ['Tích cực',   'breadth-sentiment--pos'] :
    decPct >= 60 ? ['Tiêu cực',   'breadth-sentiment--neg'] :
                   ['Trung tính', 'breadth-sentiment--neu'];

  return `
    <div class="breadth-row${divider ? ' breadth-row--divider' : ''}">
      <div class="breadth-header">
        <span class="breadth-title">${exchange} <span class="breadth-universe">${d.total} mã</span></span>
        <span class="breadth-sentiment ${sentimentCls}">${sentiment}</span>
      </div>
      <div class="breadth-track" role="img" aria-label="${exchange} — Tăng ${d.advance}, Giảm ${d.decline}, Đứng ${d.unchanged}">
        <div class="breadth-seg breadth-seg--advance" style="width:${advPct}%" title="Tăng: ${d.advance} (${advPct}%)"></div>
        <div class="breadth-seg breadth-seg--unchanged" style="width:${unchPct}%" title="Đứng: ${d.unchanged} (${unchPct}%)"></div>
        <div class="breadth-seg breadth-seg--decline" style="width:${decPct}%" title="Giảm: ${d.decline} (${decPct}%)"></div>
      </div>
      <div class="breadth-legend">
        <span class="breadth-chip breadth-chip--advance">${d.advance} tăng</span>
        <span class="breadth-chip breadth-chip--unchanged">${d.unchanged} đứng</span>
        <span class="breadth-chip breadth-chip--decline">${d.decline} giảm</span>
        ${d.ceiling > 0 ? `<span class="breadth-chip breadth-chip--ceiling">${d.ceiling} trần</span>` : ''}
        ${d.floor   > 0 ? `<span class="breadth-chip breadth-chip--floor">${d.floor} sàn</span>`   : ''}
      </div>
    </div>
  `;
}
