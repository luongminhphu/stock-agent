/**
 * portfolio-renderer.js
 * Owner: modules/portfolio
 * Responsibility: được pass data từ portfolio-loader, render toàn bộ holdings UI.
 *
 * Không fetch, không có side-effect ngoài DOM mutation.
 *
 * Features:
 *   - Renders cả 2 tab: Trades (positions thực tế) và Thesis (thesis-based).
 *   - Mỗi tab có header lưới (7 cột) và một <tbody> chứa rows.
 *   - tab badge (N) khi tab có errors
 *   - cell-error marker cho missing critical data trong row
 *
 * Wave 2 — QuickTrade modal integration:
 *   - <tr> rows mang data-ticker và data-thesis-id để injectTradeButtons() pick up
 *   - renderPortfolio() gọi injectTradeButtons(tbody, opts) sau khi inject HTML
 *
 * Field mapping (API → renderer):
 *   Trades  /portfolio/trades  → res.positions[]  fields: qty, avg_cost,
 *           current_price, unrealized_pnl, unrealized_pct, thesis_id
 *   Thesis  /portfolio         → res.positions[]  fields: quantity, avg_cost,
 *           current_price, pnl_abs, pnl_pct, last_verdict, score, score_tier
 */

import { fmt, fmtPct, esc } from '../../utils/format.js';
import * as QuickTrade       from './quick-trade.js';

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------

/**
 * @param {HTMLElement} container
 * @param {{ trades: object|null, thesis: object|null }} data
 */
export function renderPortfolio(container, { trades, thesis }) {
  const tradesRows = _buildTradesRows(trades);
  const thesisRows = _buildThesisRows(thesis);

  const errorCountTrades = tradesRows.filter(r => r.hasError).length;
  const errorCountThesis = thesisRows.filter(r => r.hasError).length;

  const tradeBadge  = errorCountTrades ? ` <span class="tab-badge">${errorCountTrades}</span>` : '';
  const thesisBadge = errorCountThesis ? ` <span class="tab-badge">${errorCountThesis}</span>` : '';

  container.innerHTML = `
    <div class="tab-bar" role="tablist" aria-label="Portfolio views">
      <button class="tab-btn active" role="tab" data-tab="trades" aria-selected="true">Trades${tradeBadge}</button>
      <button class="tab-btn" role="tab" data-tab="thesis" aria-selected="false">Thesis${thesisBadge}</button>
    </div>

    <div class="tab-content active" data-tab-content="trades">
      ${_buildHoldingsTable(tradesRows, 'trades')}
    </div>
    <div class="tab-content" data-tab-content="thesis">
      ${_buildHoldingsTable(thesisRows, 'thesis')}
    </div>
  `;

  _bindTabSwitching(container);
  _injectAllTradeButtons(container);
}

// ---------------------------------------------------------------------------
// Private: Tab switching
// ---------------------------------------------------------------------------
function _bindTabSwitching(wrap) {
  wrap.querySelectorAll('[data-tab]').forEach(btn => {
    btn.addEventListener('click', () => {
      wrap.querySelectorAll('[data-tab]').forEach(b => {
        b.classList.remove('active');
        b.setAttribute('aria-selected', 'false');
      });
      wrap.querySelectorAll('[data-tab-content]').forEach(c => c.classList.remove('active'));
      btn.classList.add('active');
      btn.setAttribute('aria-selected', 'true');
      wrap.querySelector(`[data-tab-content="${btn.dataset.tab}"]`)?.classList.add('active');
    });
  });
}

// ---------------------------------------------------------------------------
// Private: inject B/S buttons vào tất cả tbody trong wrap
// ---------------------------------------------------------------------------
function _injectAllTradeButtons(wrap) {
  wrap.querySelectorAll('[data-holdings-tbody]').forEach(tbody => {
    const fromThesisTab = tbody.dataset.holdingsTbody === 'thesis';
    QuickTrade.injectTradeButtons(tbody, { fromThesisTab });
  });
}

// ---------------------------------------------------------------------------
// Private: build rows from /portfolio/trades (PnlService)
// API returns: { positions: [{ticker, qty, avg_cost, current_price,
//               unrealized_pnl, unrealized_pct, thesis_id, ...}] }
// ---------------------------------------------------------------------------
function _buildTradesRows(data) {
  // Accept both `positions` (actual API) and legacy `holdings`
  const list = data?.positions ?? data?.holdings;
  if (!Array.isArray(list) || list.length === 0) return [];

  return list.map(h => {
    const errors = [];

    const ticker    = h.ticker ?? '';
    const qty       = h.qty ?? h.quantity ?? null;
    const avgCost   = h.avg_cost ?? h.average_cost ?? null;
    const currPrice = h.current_price ?? h.curr_price ?? null;
    // Trades endpoint uses unrealized_pnl / unrealized_pct
    const pnlAbs    = h.unrealized_pnl ?? h.pnl_abs ?? h.pnl ?? null;
    const pnlPct    = h.unrealized_pct ?? h.pnl_pct ?? h.return_pct ?? null;
    const thesisId  = h.thesis_id ?? null;

    if (!ticker)          errors.push('ticker');
    if (qty == null)      errors.push('qty');
    if (avgCost == null)  errors.push('avg_cost');
    if (currPrice == null) errors.push('curr_price');

    const pnlSign    = (pnlAbs ?? 0) >= 0 ? 'positive' : 'negative';
    const thesisAttr = thesisId ? `data-thesis-id="${thesisId}"` : '';
    const thesisRef  = thesisId
      ? `<span class="thesis-tag" title="Thesis #${thesisId}">#${thesisId}</span>`
      : '<span class="muted">—</span>';

    return {
      hasError: errors.length > 0,
      html: `
        <tr data-ticker="${esc(ticker)}" ${thesisAttr}>
          <td class="col-ticker"><strong>${esc(ticker)}</strong>${errors.includes('ticker') ? ' <span class="cell-error" title="Thiếu ticker">⚠</span>' : ''}</td>
          <td class="col-qty">${qty != null ? fmt(qty) : '<span class="cell-error" title="Thiếu qty">⚠</span>'}</td>
          <td class="col-price">${avgCost != null ? fmt(avgCost) : '<span class="cell-error" title="Thiếu avg_cost">⚠</span>'}</td>
          <td class="col-price">${currPrice != null ? fmt(currPrice) : '<span class="cell-error" title="Thiếu curr_price">⚠</span>'}</td>
          <td class="col-pnl ${pnlSign}">${pnlAbs != null ? fmt(pnlAbs) : '<span class="muted">—</span>'}</td>
          <td class="col-pct ${pnlSign}">${pnlPct != null ? fmtPct(pnlPct) : '<span class="muted">—</span>'}</td>
          <td class="col-thesis">${thesisRef}</td>
          <td class="col-action"></td>
        </tr>
      `,
    };
  });
}

// ---------------------------------------------------------------------------
// Private: build rows from /portfolio (DashboardService / PortfolioQueryService)
// API returns: { positions: [{ticker, quantity, avg_cost, current_price,
//               pnl_pct, pnl_abs, last_verdict, score, score_tier, thesis_id, ...}] }
// ---------------------------------------------------------------------------
function _buildThesisRows(data) {
  // Accept both `positions` (actual API) and legacy `holdings`
  const list = data?.positions ?? data?.holdings;
  if (!Array.isArray(list) || list.length === 0) return [];

  return list.map(h => {
    const errors   = [];
    const ticker   = h.ticker    ?? '';
    const thesisId = h.thesis_id ?? null;
    // Thesis endpoint uses `quantity` not `qty`
    const qty      = h.quantity  ?? h.qty ?? null;
    const avgCost  = h.avg_cost  ?? null;
    const currPrice = h.current_price ?? h.curr_price ?? null;
    const pnlAbs   = h.pnl_abs   ?? h.unrealized_pnl ?? null;
    const pnlPct   = h.pnl_pct   ?? h.unrealized_pct ?? null;
    const verdict  = h.last_verdict ?? h.verdict ?? null;
    const score    = h.score     ?? null;
    const scoreTier = h.score_tier ?? null;

    if (!ticker)  errors.push('ticker');

    const pnlSign = (pnlAbs ?? 0) >= 0 ? 'positive' : 'negative';

    const verdictLabel = verdict
      ? `<span class="badge ${String(verdict).toLowerCase()}">${esc(String(verdict).toUpperCase())}</span>`
      : '<span class="muted">—</span>';

    const scoreLabel = score != null
      ? `<span class="score-chip ${_scoreClass(score)}" title="${scoreTier ?? ''}">${Math.round(score)}</span>`
      : '<span class="muted">—</span>';

    const thesisAttr = thesisId ? `data-thesis-id="${thesisId}"` : '';
    const thesisRef  = thesisId
      ? `<span class="thesis-tag">#${thesisId}</span>`
      : '<span class="muted">—</span>';

    return {
      hasError: errors.length > 0,
      html: `
        <tr data-ticker="${esc(ticker)}" ${thesisAttr}>
          <td class="col-ticker"><strong>${esc(ticker)}</strong>${errors.includes('ticker') ? ' <span class="cell-error" title="Thiếu ticker">⚠</span>' : ''}</td>
          <td class="col-qty">${qty != null ? fmt(qty) : '<span class="muted">—</span>'}</td>
          <td class="col-price">${avgCost != null ? fmt(avgCost) : '<span class="muted">—</span>'}</td>
          <td class="col-price">${currPrice != null ? fmt(currPrice) : '<span class="muted">—</span>'}</td>
          <td class="col-pnl ${pnlSign}">${pnlAbs != null ? fmt(pnlAbs) : '<span class="muted">—</span>'}</td>
          <td class="col-pct ${pnlSign}">${pnlPct != null ? fmtPct(pnlPct) : '<span class="muted">—</span>'}</td>
          <td class="col-thesis">
            ${verdictLabel}
            ${scoreLabel}
            ${thesisRef}
          </td>
          <td class="col-action"></td>
        </tr>
      `,
    };
  });
}

// ---------------------------------------------------------------------------
// Private: table builder (shared by both tabs)
// ---------------------------------------------------------------------------
function _buildHoldingsTable(rows, tabKey) {
  const isEmpty = rows.length === 0;
  const emptyMsg = tabKey === 'trades'
    ? 'Chưa có vị thế nào.' : 'Không có thesis active nào có vị thế.';

  return `
    <table class="holdings-table">
      <thead>
        <tr>
          <th class="col-ticker">Ticker</th>
          <th class="col-qty">SL (cp)</th>
          <th class="col-price">Giá vốn</th>
          <th class="col-price">Thị giá</th>
          <th class="col-pnl">P&amp;L (₫)</th>
          <th class="col-pct">P&amp;L (%)</th>
          <th class="col-thesis">${tabKey === 'trades' ? 'Thesis' : 'Verdict / Score'}</th>
          <th class="col-action"></th>
        </tr>
      </thead>
      <tbody data-holdings-tbody="${tabKey}">
        ${isEmpty
          ? `<tr><td colspan="8" class="empty-state">${emptyMsg}</td></tr>`
          : rows.map(r => r.html).join('')
        }
      </tbody>
    </table>
  `;
}

// ---------------------------------------------------------------------------
// Private: score tier → CSS class
// ---------------------------------------------------------------------------
function _scoreClass(s) {
  if (s == null) return '';
  if (s >= 86) return 'score-high';
  if (s >= 71) return 'score-good';
  if (s >= 51) return 'score-mid';
  if (s >= 31) return 'score-warn';
  return 'score-low';
}
