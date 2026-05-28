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
  const sign = val >= 0 ? '+' : '';
  return `${sign}${val.toFixed(2)}%`;
}

function pnlClass(val) {
  if (val == null) return '';
  return val >= 0 ? 'pnl-positive' : 'pnl-negative';
}

function pnlIcon(val) {
  if (val == null) return '⚪';
  return val >= 0 ? '🟢' : '🔴';
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

// ---------------------------------------------------------------------------
// Error Banner
// ---------------------------------------------------------------------------
function badgeHTML(count) {
  if (!count) return '';
  return `<span class="perr-tab-badge" aria-label="${count} lỗi">${count}</span>`;
}

function renderErrorBanner(errors) {
  if (!errors.length) return '';

  const criticals = errors.filter(e => e.severity === 'critical');
  const warnings  = errors.filter(e => e.severity === 'warning');
  const infos     = errors.filter(e => e.severity === 'info');

  const summary = criticals.length
    ? `🔴 ${criticals.length} lỗi nghiêm trọng`
    : warnings.length
      ? `🟡 ${warnings.length} cảnh báo dữ liệu`
      : `🔵 ${infos.length} thông tin`;

  const items = errors.map(e => {
    const cls = e.severity === 'critical' ? 'perr-critical'
              : e.severity === 'warning'  ? 'perr-warning'
              : 'perr-info';
    return `<li class="perr-item ${cls}">${e.message}</li>`;
  }).join('');

  return `
    <div class="perr-banner" role="alert">
      <button class="perr-toggle" aria-expanded="false">
        <span class="perr-summary">${summary}</span>
        <span class="perr-chevron">▾</span>
      </button>
      <ul class="perr-list" hidden>${items}</ul>
    </div>`;
}

function wireBannerToggle(banner) {
  const btn  = banner.querySelector('.perr-toggle');
  const list = banner.querySelector('.perr-list');
  if (!btn || !list) return;
  btn.addEventListener('click', () => {
    const expanded = btn.getAttribute('aria-expanded') === 'true';
    btn.setAttribute('aria-expanded', String(!expanded));
    list.hidden = expanded;
  });
}

// ---------------------------------------------------------------------------
// Trades tab renderer
// ---------------------------------------------------------------------------
function renderTradesTab(data, errors) {
  if (!data) return '<p class="empty-state">Không thể tải dữ liệu giao dịch.</p>';
  const positions = data.positions ?? [];
  if (!positions.length) return '<p class="empty-state">Chưa có vị thế nào. Dùng <code>/buy</code> trên Discord để bắt đầu.</p>';

  const totalPnl  = data.total_unrealized_pnl ?? 0;
  const totalPct  = data.total_unrealized_pct ?? 0;
  const totalCost = data.total_cost_basis ?? 0;
  const totalMkt  = data.total_market_value ?? 0;

  const missingPriceTickers = new Set(positions.filter(p => p.current_price == null).map(p => p.ticker));

  const rows = positions.map(p => {
    const pct      = p.unrealized_pct ?? null;
    const hasError = missingPriceTickers.has(p.ticker);
    const thesisAttr = p.thesis_id ? ` data-thesis-id="${p.thesis_id}"` : '';

    const thesisWarning   = p.thesis_id && p.thesis_status && p.thesis_status !== 'active';
    const thesisWarnTitle = thesisWarning
      ? `Thesis #${p.thesis_id} đã ${p.thesis_status} — cần review vị thế`
      : '';

    return `
      <tr class="${hasError ? 'row-data-error' : ''}${thesisWarning ? ' row-thesis-warning' : ''}" data-ticker="${p.ticker}"${thesisAttr}>
        <td class="col-ticker col-center">
          <strong>${p.ticker}</strong>${hasError ? ' <span class="cell-error-dot" title="Thiếu dữ liệu giá">●</span>' : ''}${thesisWarning ? ` <span class="thesis-warn-badge" title="${thesisWarnTitle}" aria-label="${thesisWarnTitle}">⚠️</span>` : ''}
        </td>
        <td class="col-action col-center"></td>
        <td class="num">${p.qty != null ? p.qty.toLocaleString('vi-VN') : '—'}</td>
        <td class="num">${fmtVnd(p.avg_cost)}</td>
        <td class="num${p.current_price == null ? ' cell-missing' : ''}">${fmtVnd(p.current_price)}</td>
        <td class="num">${fmtVnd(p.cost_basis)}</td>
        <td class="num">${fmtVnd(p.market_value)}</td>
        <td class="num ${pnlClass(p.unrealized_pnl)}">${pnlIcon(p.unrealized_pnl)} ${fmtVnd(p.unrealized_pnl)}</td>
        <td class="num ${pnlClass(pct)}">${fmtPct(pct)}</td>
      </tr>`;
  }).join('');

  const banner = renderErrorBanner(errors.filter(e => e.scope === 'trades'));
  const exposureBar = renderExposureBar(positions, totalMkt);
  return `
    ${banner}
    ${exposureBar}
    <div class="portfolio-summary">
      <span class="summary-chip">${pnlIcon(totalPnl)} P&amp;L: <strong class="${pnlClass(totalPnl)}">${fmtVnd(totalPnl)}</strong> (${fmtPct(totalPct)})</span>
      <span class="summary-chip">Vốn: <strong>${fmtVnd(totalCost)}</strong></span>
      <span class="summary-chip">Thị giá: <strong>${fmtVnd(totalMkt)}</strong></span>
      <span class="summary-chip">Vị thế: <strong>${positions.length}</strong></span>
    </div>
    <div class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th class="col-ticker col-center">Ticker</th>
            <th class="col-action col-center">Hành động</th>
            <th class="num">Khối lượng</th>
            <th class="num">Giá vốn TB</th>
            <th class="num">Giá HT</th>
            <th class="num">Chi phí vốn</th>
            <th class="num">Thị giá</th>
            <th class="num">P&amp;L</th>
            <th class="num">%P&amp;L</th>
          </tr>
        </thead>
        <tbody data-holdings-tbody data-tab="trades">${rows}</tbody>
      </table>
    </div>`;
}

// ---------------------------------------------------------------------------
// Thesis tab renderer
// ---------------------------------------------------------------------------
const VERDICT_BADGE = {
  BULLISH:   { icon: '🐂', cls: 'badge-bullish'  },
  BEARISH:   { icon: '🐻', cls: 'badge-bearish'  },
  NEUTRAL:   { icon: '⚖️', cls: 'badge-neutral'  },
  WATCHLIST: { icon: '👁', cls: 'badge-watchlist' },
};

function renderThesisTab(data, errors) {
  if (!data) return '<p class="empty-state">Không thể tải dữ liệu thesis.</p>';
  const positions = data.positions ?? [];
  if (!positions.length) return '<p class="empty-state">Chưa có thesis nào đang active với vị thế mở.</p>';

  const rows = positions.map(p => {
    const verdict   = VERDICT_BADGE[p.verdict] ?? VERDICT_BADGE.NEUTRAL;
    const pct       = p.unrealized_pct ?? null;
    const thesisId  = p.id ?? p.thesis_id;
    const thesisAttr = thesisId ? ` data-thesis-id="${thesisId}"` : '';

    return `
      <tr data-ticker="${p.ticker}"${thesisAttr}>
        <td class="col-ticker col-center"><strong>${p.ticker}</strong></td>
        <td class="col-action col-center"></td>
        <td class="col-center"><span class="verdict-badge ${verdict.cls}">${verdict.icon} ${p.verdict ?? '—'}</span></td>
        <td class="num">${fmtVnd(p.entry_price ?? p.avg_cost)}</td>
        <td class="num">${fmtVnd(p.current_price)}</td>
        <td class="num ${pnlClass(pct)}">${fmtPct(pct)}</td>
        <td class="num">${p.score != null ? p.score.toFixed(1) : '—'}</td>
      </tr>`;
  }).join('');

  const n       = positions.length;
  const winning = positions.filter(p => (p.unrealized_pct ?? 0) > 0).length;
  const losing  = positions.filter(p => (p.unrealized_pct ?? 0) < 0).length;

  const totalPnlPct = data.total_unrealized_pct ?? null;
  const summaryPnl = totalPnlPct != null
    ? `${pnlIcon(totalPnlPct)} P&amp;L avg: <strong class="${pnlClass(totalPnlPct)}">${fmtPct(totalPnlPct)}</strong>`
    : '';
  const banner = renderErrorBanner(errors.filter(e => e.scope === 'thesis'));

  return `
    ${banner}
    <div class="portfolio-summary">
      ${summaryPnl ? `<span class="summary-chip">${summaryPnl}</span>` : ''}
      <span class="summary-chip">Theses: <strong>${n}</strong></span>
      <span class="summary-chip">🟢 Lời: <strong>${winning}</strong></span>
      <span class="summary-chip">🔴 Lỗ: <strong>${losing}</strong></span>
    </div>
    <div class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th class="col-ticker col-center">Ticker</th>
            <th class="col-action col-center">Hành động</th>
            <th class="col-center">Verdict</th>
            <th class="num">Entry</th>
            <th class="num">Giá HT</th>
            <th class="num">P&amp;L %</th>
            <th class="num">Score</th>
          </tr>
        </thead>
        <tbody data-holdings-tbody data-tab="thesis">${rows}</tbody>
      </table>
    </div>`;
}

// ---------------------------------------------------------------------------
// Main render — 2-tab layout
// ---------------------------------------------------------------------------
export function renderPortfolio(wrap, { trades, thesis }) {
  const prevActivePane = wrap.querySelector('.portfolio-tab.active')
    ?.getAttribute('aria-controls') ?? 'portfolioTradesPane';

  const errors       = collectErrors(trades, thesis);
  const tradesErrors = errors.filter(e => e.scope === 'trades');
  const thesisErrors = errors.filter(e => e.scope === 'thesis');

  const tradesBadge = badgeHTML(tradesErrors.filter(e => e.severity !== 'info').length);
  const thesisBadge = badgeHTML(thesisErrors.filter(e => e.severity !== 'info').length);

  const tradesHTML = renderTradesTab(trades, errors);
  const thesisHTML = renderThesisTab(thesis, errors);

  wrap.innerHTML = `
    <div class="portfolio-tab-bar" role="tablist" aria-label="Portfolio view">
      <button class="portfolio-tab active" role="tab" aria-selected="true"
        aria-controls="portfolioTradesPane" data-tab="portfolioTradesPane">
        📊 Trades${tradesBadge}
      </button>
      <button class="portfolio-tab" role="tab" aria-selected="false"
        aria-controls="portfolioThesisPane" data-tab="portfolioThesisPane">
        📋 Thesis${thesisBadge}
      </button>
    </div>

    <div id="portfolioTradesPane" class="portfolio-pane" role="tabpanel">
      ${tradesHTML}
    </div>
    <div id="portfolioThesisPane" class="portfolio-pane hidden" role="tabpanel">
      ${thesisHTML}
    </div>`;

  if (prevActivePane !== 'portfolioTradesPane') {
    const tabs  = wrap.querySelectorAll('.portfolio-tab');
    const panes = wrap.querySelectorAll('.portfolio-pane');

    tabs.forEach(t => {
      const isTarget = t.getAttribute('aria-controls') === prevActivePane;
      t.classList.toggle('active', isTarget);
      t.setAttribute('aria-selected', String(isTarget));
    });
    panes.forEach(p => {
      p.classList.toggle('hidden', p.id !== prevActivePane);
    });
  }

  wrap.querySelectorAll('.perr-banner').forEach(wireBannerToggle);
  _injectAllTradeButtons(wrap);

  wrap.querySelectorAll('.portfolio-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      const targetId = btn.getAttribute('aria-controls');
      wrap.querySelectorAll('.portfolio-tab').forEach(t => {
        const active = t === btn;
        t.classList.toggle('active', active);
        t.setAttribute('aria-selected', String(active));
      });
      wrap.querySelectorAll('.portfolio-pane').forEach(p => {
        p.classList.toggle('hidden', p.id !== targetId);
      });
    });
  });
}

// ---------------------------------------------------------------------------
// Private: inject B/S buttons vào tất cả tbody trong wrap
// ---------------------------------------------------------------------------
function _injectAllTradeButtons(wrap) {
  const QuickTrade = window.QuickTrade;
  if (!QuickTrade?.injectTradeButtons) return;

  wrap.querySelectorAll('[data-holdings-tbody]').forEach(tbody => {
    const fromThesisTab = tbody.getAttribute('data-tab') === 'thesis';
    QuickTrade.injectTradeButtons(tbody, { fromThesisTab });
  });
}
