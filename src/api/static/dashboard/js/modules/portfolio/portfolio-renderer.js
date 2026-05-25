/**
 * portfolio-renderer.js
 * Owner: readmodel segment (frontend projection)
 *
 * Renders the Holdings table and Trades tab inside the Portfolio panel.
 * Reads from /api/v1/readmodel/dashboard/portfolio/trades (PnlService)
 * and /api/v1/readmodel/dashboard/portfolio (PortfolioQueryService).
 *
 * Gap 3 B3: reads thesis_status from API response and renders a ⚠️ warning
 * badge on Holdings rows whose linked thesis has been invalidated or closed.
 */

import { formatVND, formatPct, formatQty } from '../../utils/format.js';
import { showToast } from '../../utils/toast.js';

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Render the full portfolio trades table into `containerEl`.
 * @param {HTMLElement} containerEl
 * @param {{ positions: PositionRow[], errors: Record<string,string>, total_unrealized_pnl: number }} data
 */
export function renderPortfolioTrades(containerEl, data) {
  if (!containerEl) return;

  const { positions = [], errors = {}, total_unrealized_pnl = 0 } = data;

  if (positions.length === 0 && Object.keys(errors).length === 0) {
    containerEl.innerHTML = _emptyState();
    return;
  }

  const rows = positions.map(_buildTradeRow).join('');
  const errorRows = Object.entries(errors).map(_buildErrorRow).join('');
  const summary = _buildSummaryRow(data);

  containerEl.innerHTML = `
    <table class="portfolio-table" aria-label="Holdings">
      <thead>
        <tr>
          <th class="col-ticker">Mã</th>
          <th class="col-qty col-right">KL</th>
          <th class="col-avg-cost col-right">Giá vốn</th>
          <th class="col-current-price col-right">Giá TT</th>
          <th class="col-cost-basis col-right">Vốn đầu tư</th>
          <th class="col-market-value col-right">Giá trị TT</th>
          <th class="col-unrealized-pnl col-right">Lãi/lỗ TT</th>
          <th class="col-unrealized-pct col-right">%</th>
        </tr>
      </thead>
      <tbody>
        ${rows}
        ${errorRows}
      </tbody>
      ${summary}
    </table>
  `;
}

// ---------------------------------------------------------------------------
// Row builders
// ---------------------------------------------------------------------------

/**
 * @typedef {{ ticker: string, qty: number, avg_cost: number, current_price: number,
 *             cost_basis: number, market_value: number, unrealized_pnl: number,
 *             unrealized_pct: number, thesis_id: number|null,
 *             thesis_status: string|null }} PositionRow
 */

/** @param {PositionRow} p */
function _buildTradeRow(p) {
  const pnlClass  = p.unrealized_pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
  const hasError  = !p.current_price || p.current_price === 0;
  const thesisAttr = p.thesis_id ? ` data-thesis-id="${p.thesis_id}"` : '';

  // Gap 3 B3: warn when thesis is no longer active
  const thesisWarning   = p.thesis_id && p.thesis_status && p.thesis_status !== 'active';
  const thesisWarnTitle = thesisWarning
    ? `Thesis #${p.thesis_id} đã ${p.thesis_status} — cần review vị thế`
    : '';

  return `
    <tr class="col-ticker${hasError ? ' row-data-error' : ''}${thesisWarning ? ' row-thesis-warning' : ''}"${thesisAttr}>
      <td class="col-ticker col-center">
        <strong>${p.ticker}</strong>${hasError ? ' <span class="cell-error-dot" title="Thiếu dữ liệu giá">●</span>' : ''}${thesisWarning ? ` <span class="thesis-warn-badge" title="${thesisWarnTitle}" aria-label="${thesisWarnTitle}">⚠️</span>` : ''}
      </td>
      <td class="col-qty col-right">${formatQty(p.qty)}</td>
      <td class="col-avg-cost col-right">${formatVND(p.avg_cost)}</td>
      <td class="col-current-price col-right">${hasError ? '<span class="text-muted">—</span>' : formatVND(p.current_price)}</td>
      <td class="col-cost-basis col-right">${formatVND(p.cost_basis)}</td>
      <td class="col-market-value col-right">${hasError ? '<span class="text-muted">—</span>' : formatVND(p.market_value)}</td>
      <td class="col-unrealized-pnl col-right ${pnlClass}">${hasError ? '<span class="text-muted">—</span>' : formatVND(p.unrealized_pnl)}</td>
      <td class="col-unrealized-pct col-right ${pnlClass}">${hasError ? '<span class="text-muted">—</span>' : formatPct(p.unrealized_pct)}</td>
    </tr>
  `;
}

/** @param {[string, string]} entry */
function _buildErrorRow([ticker, errMsg]) {
  return `
    <tr class="row-data-error">
      <td class="col-ticker"><strong>${ticker}</strong> <span class="cell-error-dot" title="${errMsg}">●</span></td>
      <td colspan="7" class="col-error-msg text-muted">${errMsg}</td>
    </tr>
  `;
}

/**
 * @param {{ total_unrealized_pnl: number, total_unrealized_pct: number,
 *           total_cost_basis: number, total_market_value: number }} data
 */
function _buildSummaryRow(data) {
  const pnlClass = data.total_unrealized_pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
  return `
    <tfoot>
      <tr class="row-summary">
        <td colspan="4"><strong>Tổng danh mục</strong></td>
        <td class="col-right">${formatVND(data.total_cost_basis)}</td>
        <td class="col-right">${formatVND(data.total_market_value)}</td>
        <td class="col-right ${pnlClass}">${formatVND(data.total_unrealized_pnl)}</td>
        <td class="col-right ${pnlClass}">${formatPct(data.total_unrealized_pct)}</td>
      </tr>
    </tfoot>
  `;
}

function _emptyState() {
  return `
    <div class="portfolio-empty-state">
      <p class="text-muted">Chưa có vị thế nào đang mở.</p>
    </div>
  `;
}
