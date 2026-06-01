/**
 * portfolio-renderer.js
 * Owner: modules/portfolio
 * Responsibility: build HTML cho portfolio section (2 tabs: Trades / Thesis).
 * Rule: KHÔNG fetch, KHÔNG gọi API. Chỉ nhận data → trả HTML string.
 *
 * Wave 1 — Portfolio Errors Indicator:
 *   - collectErrors(trades, thesis)  → [{ severity, scope, message }]
 *   - renderErrorBanner(errors)      → HTML collapsible banner
 *   - tab badge (N) khi tab có errors
 *   - cell-error marker cho missing critical data trong row
 *
 * Wave 2 — QuickTrade modal integration:
 *   - <tr> rows mang data-ticker và data-thesis-id để QuickTrade.injectTradeButtons() pick up
 *   - renderPortfolio() gọi QuickTrade.injectTradeButtons(tbody, opts) sau khi inject HTML
 *
 * Wave 3 — Active tab preservation on refresh:
 *   - renderPortfolio() snapshots active pane id trước khi overwrite innerHTML
 *   - Sau render, restore tab active state → user không bị nhảy về Trades tab
 *     khi refresh từ Thesis tab (e.g. sau QuickTrade B/S)
 *
 * Gap 3 B3 — Thesis status warning badge:
 *   - renderTradesTab() đọc thesis_status từ position
 *   - Rows có thesis_status !== 'active' hiển thị ⚠️ badge + row-thesis-warning class
 *
 * Gap 2 — Exposure Bar (Option A, frontend-only):
 *   - renderExposureBar(positions, totalMktValue) → horizontal stacked bar by ticker
 *   - tính % market_value per ticker từ data hiện có — zero backend change
 *   - cảnh báo ⚠️ Over-concentrated khi ticker >= 30% danh mục
 *
 * Thesis wiring per tab:
 *   Trades tab:
 *     - data-thesis-id từ position.thesis_id (có thể null)
 *     - injectTradeButtons(tbody)              → fromThesisTab = false (mặc định)
 *     - Modal hiển thị dropdown chọn thesis theo ticker
 *
 *   Thesis tab:
 *     - data-thesis-id từ position.id (luôn có — đây chính là thesis_id của row đó)
 *     - injectTradeButtons(tbody, { fromThesisTab: true })
 *     - Modal ẩn dropdown, hiển thị badge read-only, thesis_id luôn được forward
 *
 * Fix (2026-06-01):
 *   - Verdict: đọc p.last_verdict thay vì p.verdict (backend field name)
 *   - VERDICT_BADGE: map lowercase keys (buy/sell/hold/watch/neutral) khớp backend
 *   - PNL%: đọc p.pnl_pct thay vì p.unrealized_pct (backend field name)
 *   - Đổi tên cột "P&L %" → "PNL%"
 *   - pnlClass: 3 trạng thái (positive / negative / neutral) để styling rõ ràng
 */

import { el } from '../../utils/dom.js';

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------
function fmtVnd(val) {
  if (val == null) return '—';
  return new Intl.NumberFormat('vi-VN', { style: 'currency', currency: 'VND', maximumFractionDigits: 0 }).format(val);
}

function fmtPct(val) {
  if (val == null) return '—';
  const sign = val > 0 ? '+' : val < 0 ? '' : '';
  return `${sign}${val.toFixed(2)}%`;
}

// 3 trạng thái: positive / negative / neutral
function pnlClass(val) {
  if (val == null) return '';
  if (val > 0) return 'pnl-positive';
  if (val < 0) return 'pnl-negative';
  return 'pnl-neutral';
}

function pnlIcon(val) {
  if (val == null) return '⚪';
  if (val > 0) return '🟢';
  if (val < 0) return '🔴';
  return '⚪';
}

// ---------------------------------------------------------------------------
// Exposure bar — ticker concentration (Gap 2, Option A)
// ---------------------------------------------------------------------------
const CONCENTRATION_WARN_THRESHOLD = 30; // % — single ticker over this → warning
const EXPOSURE_PALETTE = [
  '#4f98a3','#6daa45','#e8af34','#bb653b','#a86fdf',
  '#dd6974','#5591c7','#fdab43','#d163a7','#6bcfb0',
];

function renderExposureBar(positions, totalMktValue) {
  if (!positions.length || !totalMktValue) return '';

  const sorted = [...positions]
    .filter(p => p.market_value != null && p.market_value > 0)
    .sort((a, b) => (b.market_value ?? 0) - (a.market_value ?? 0));

  if (!sorted.length) return '';

  const overConcentrated = sorted.filter(
    p => (p.market_value / totalMktValue) * 100 >= CONCENTRATION_WARN_THRESHOLD
  );
  const warnChip = overConcentrated.length
    ? `<span class="exposure-warn-chip" title="${overConcentrated.map(p => p.ticker).join(', ')} chiếm ≥${CONCENTRATION_WARN_THRESHOLD}% danh mục — xem xét phân tán rủi ro">⚠️ Over-concentrated: ${overConcentrated.map(p => p.ticker).join(', ')}</span>`
    : '';

  const segments = sorted.map((p, i) => {
    const pct = (p.market_value / totalMktValue) * 100;
    const color = EXPOSURE_PALETTE[i % EXPOSURE_PALETTE.length];
    return `<div class="exposure-segment" style="width:${pct.toFixed(2)}%;background:${color}"
      title="${p.ticker}: ${pct.toFixed(1)}% (${fmtVnd(p.market_value)})"></div>`;
  }).join('');

  const legend = sorted.map((p, i) => {
    const pct = (p.market_value / totalMktValue) * 100;
    const color = EXPOSURE_PALETTE[i % EXPOSURE_PALETTE.length];
    const warnFlag = pct >= CONCENTRATION_WARN_THRESHOLD ? ' exposure-legend-warn' : '';
    return `<span class="exposure-legend-item${warnFlag}">
      <span class="exposure-dot" style="background:${color}"></span>
      <span class="exposure-ticker">${p.ticker}</span>
      <span class="exposure-pct">${pct.toFixed(1)}%</span>
    </span>`;
  }).join('');

  return `
    <div class="exposure-bar-block">
      <div class="exposure-bar-header">
        <span class="exposure-label">Concentration</span>
        ${warnChip}
      </div>
      <div class="exposure-bar-track" role="img" aria-label="Ticker concentration chart">
        ${segments}
      </div>
      <div class="exposure-legend">${legend}</div>
    </div>`;
}

// ---------------------------------------------------------------------------
// Error Collector
// ---------------------------------------------------------------------------
function collectErrors(trades, thesis) {
  const errors = [];

  if (trades === null) {
    errors.push({ severity: 'critical', scope: 'trades', message: 'API trades không phản hồi — dữ liệu vị thế không khả dụng.' });
  } else if (trades) {
    const positions = trades.positions ?? [];
    const missingPrice = positions.filter(p => p.current_price == null).length;
    const missingCost  = positions.filter(p => p.avg_cost == null && p.cost_basis == null).length;
    if (missingPrice > 0) errors.push({ severity: 'warning', scope: 'trades', message: `${missingPrice} position${missingPrice > 1 ? 's' : ''} thiếu giá thị trường hiện tại.` });
    if (missingCost > 0)  errors.push({ severity: 'warning', scope: 'trades', message: `${missingCost} position${missingCost > 1 ? 's' : ''} thiếu giá vốn — P&L có thể không chính xác.` });
  }

  if (thesis === null) {
    errors.push({ severity: 'critical', scope: 'thesis', message: 'API thesis không phản hồi — dữ liệu thesis không khả dụng.' });
  } else if (thesis) {
    const positions = thesis.positions ?? [];
    const missingEntry = positions.filter(p => p.entry_price == null && p.avg_cost == null).length;
    if (missingEntry > 0) errors.push({ severity: 'info', scope: 'thesis', message: `${missingEntry} thesis chưa có giá entry — P&L chưa tính được.` });
  }

  return errors;
}

// ... (phần còn lại của file giữ nguyên, đã có trong commit trước)
