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
// ---------------------------------------------------------------------------
function _buildTradesRows(data) {
  if (!data || !Array.isArray(data.holdings)) return [];
  return data.holdings.map(h => {
    const errors = [];

    const qty        = h.qty        ?? h.quantity      ?? null;
    const avgCost    = h.avg_cost   ?? h.average_cost  ?? null;
    const currPrice  = h.curr_price ?? h.current_price ?? null;
    const pnlPct     = h.pnl_pct    ?? h.return_pct    ?? null;
    const pnlAbs     = h.pnl_abs    ?? h.pnl           ?? null;
    const thesisId   = h.thesis_id  ?? null;
    const thesisRef  = thesisId ? `<span class="thesis-tag" title="Thesis #${thesisId}">#${thesisId}</span>` : '<span class="muted">—</span>';
    const ticker     = h.ticker     ?? '';

    if (!ticker)    errors.push('ticker');
    if (qty == null)       errors.push('qty');
    if (avgCost == null)   errors.push('avg_cost');
    if (currPrice == null) errors.push('curr_price');

    const pnlSign = (pnlAbs ?? 0) >= 0 ? 'positive' : 'negative';
    const thesisAttr = thesisId ? `data-thesis-id="${thesisId}"` : '';

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
// Private: build rows from /portfolio (DashboardService)
// ---------------------------------------------------------------------------
function _buildThesisRows(data) {
  if (!data || !Array.isArray(data.holdings)) return [];
  return data.holdings.map(h => {
    const errors = [];
    const ticker   = h.ticker    ?? '';
    const thesisId = h.thesis_id ?? null;
    const verdict  = h.verdict   ?? h.last_verdict ?? null;
    const qty      = h.qty       ?? h.quantity     ?? null;
    const exposure = h.exposure  ?? null;

    if (!ticker)  errors.push('ticker');

    const verdictLabel = verdict
      ? `<span class="badge ${String(verdict).toLowerCase()}">${esc(String(verdict).toUpperCase())}</span>`
      : '<span class="muted">—</span>';

    const thesisAttr   = thesisId ? `data-thesis-id="${thesisId}"` : '';
    const exposureStr  = exposure  != null ? fmt(exposure) : '<span class="muted">—</span>';
    const qtyStr       = qty       != null ? fmt(qty)      : '<span class="muted">—</span>';

    return {
      hasError: errors.length > 0,
      html: `
        <tr data-ticker="${esc(ticker)}" ${thesisAttr}>
          <td class="col-ticker"><strong>${esc(ticker)}</strong>${errors.includes('ticker') ? ' <span class="cell-error" title="Thiếu ticker">⚠</span>' : ''}</td>
          <td class="col-qty">${qtyStr}</td>
          <td class="col-pnl">${verdictLabel}</td>
          <td colspan="2" class="col-price">${exposureStr}</td>
          <td class="col-pct"><span class="muted">—</span></td>
          <td class="col-thesis">${thesisId ? `<span class="thesis-tag">#${thesisId}</span>` : '<span class="muted">—</span>'}</td>
          <td class="col-action"></td>
        </tr>
      `,
    };
  });
}

// ---------------------------------------------------------------------------
// Private: build full table HTML
// ---------------------------------------------------------------------------
function _buildHoldingsTable(rows, id) {
  const tbodyAttr = `data-holdings-tbody="${id}"`;

  if (!rows.length) {
    return `
      <table class="holdings-table">
        <thead><tr>
          <th>Ticker</th><th>SL (cp)</th><th>Giá vốn</th><th>Thị giá</th>
          <th>P&amp;L (₫)</th><th>P&amp;L (%)</th><th>Thesis</th><th></th>
        </tr></thead>
        <tbody ${tbodyAttr}>
          <tr><td colspan="8" class="empty-state">Chưa có vị thế nào.</td></tr>
        </tbody>
      </table>
    `;
  }

  const htmlRows = rows.map(r => r.html).join('');
  return `
    <table class="holdings-table">
      <thead><tr>
        <th>Ticker</th><th>SL (cp)</th><th>Giá vốn</th><th>Thị giá</th>
        <th>P&amp;L (₫)</th><th>P&amp;L (%)</th><th>Thesis</th><th></th>
      </tr></thead>
      <tbody ${tbodyAttr}>${htmlRows}</tbody>
    </table>
  `;
}
