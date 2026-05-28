/**
 * modules/market/breadth.js
 * Owner: market segment (data) / dashboard adapter (render)
 *
 * Fetches GET /api/v1/market/breadth and renders a horizontal
 * advance/decline/unchanged bar into #marketBreadthBar.
 *
 * Graceful degradation:
 *   - 503 (adapter not configured): silently hidden — not an error.
 *   - 502 / network error: hidden with console.warn.
 *   - Empty registry: hidden.
 */

import { marketApiBase, getJson } from '../../api/client.js';

const ELEMENT_ID = 'marketBreadthBar';

/**
 * Fetch breadth for a given exchange scope and render the bar.
 * @param {'HOSE'|'HNX'|'UPCOM'|'ALL'} [exchange='HOSE']
 */
export async function loadMarketBreadth(exchange = 'HOSE') {
  const wrap = document.getElementById(ELEMENT_ID);
  if (!wrap) return;

  try {
    const data = await getJson(`${marketApiBase()}/breadth?exchange=${exchange}`);
    if (!data || data.total === 0) {
      wrap.classList.add('hidden');
      return;
    }
    renderBreadthBar(wrap, data, exchange);
  } catch (err) {
    // 503 = adapter not configured (Wave 1 stub) — expected, stay silent
    if (err.message?.startsWith('503')) {
      wrap.classList.add('hidden');
      return;
    }
    console.warn('[breadth] loadMarketBreadth failed:', err.message);
    wrap.classList.add('hidden');
  }
}

/**
 * @param {HTMLElement} wrap
 * @param {object} d  BreadthResponse shape
 * @param {string} exchange
 */
function renderBreadthBar(wrap, d, exchange) {
  const advPct  = d.advance_pct   ?? 0;
  const decPct  = d.decline_pct   ?? 0;
  const unchPct = d.unchanged_pct ?? 0;

  // Sentiment label
  const sentiment =
    advPct >= 60 ? '\uD83D\uDFE2 Tích cực' :
    decPct >= 60 ? '\uD83D\uDD34 Tiêu cực' :
    '\uD83D\uDFE1 Trung tính';

  wrap.classList.remove('hidden');
  wrap.innerHTML = `
    <div class="breadth-header">
      <span class="breadth-title">Breadth ${exchange} <span class="breadth-universe muted">(registry ${d.total} mã)</span></span>
      <span class="breadth-sentiment">${sentiment}</span>
    </div>
    <div class="breadth-track" role="img" aria-label="Advance ${d.advance}, Decline ${d.decline}, Unchanged ${d.unchanged}">
      <div class="breadth-seg breadth-seg--advance" style="width:${advPct}%" title="Tăng: ${d.advance} (${advPct}%)"></div>
      <div class="breadth-seg breadth-seg--unchanged" style="width:${unchPct}%" title="Đứng: ${d.unchanged} (${unchPct}%)"></div>
      <div class="breadth-seg breadth-seg--decline" style="width:${decPct}%" title="Giảm: ${d.decline} (${decPct}%)"></div>
    </div>
    <div class="breadth-legend">
      <span class="breadth-chip breadth-chip--advance">⬆ ${d.advance} tăng</span>
      <span class="breadth-chip breadth-chip--unchanged">● ${d.unchanged} đứng</span>
      <span class="breadth-chip breadth-chip--decline">⬇ ${d.decline} giảm</span>
      ${d.ceiling > 0 ? `<span class="breadth-chip breadth-chip--ceiling">🔼 Trần ${d.ceiling}</span>` : ''}
      ${d.floor   > 0 ? `<span class="breadth-chip breadth-chip--floor">🔽 Sàn ${d.floor}</span>`   : ''}
    </div>
  `;
}
