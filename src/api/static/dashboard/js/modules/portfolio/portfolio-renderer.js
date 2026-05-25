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
    errors.push({ severity: 'critical', scope: 'thesis', message: 'API thesis portfolio không phản hồi — dữ liệu thesis không khả dụng.' });
  } else if (thesis) {
    const positions = thesis.positions ?? [];
    const missingEntry = positions.filter(p => p.entry_price == null && p.avg_cost == null).length;
    const missingPrice = positions.filter(p => p.current_price == null).length;
    const missingScore = positions.filter(p => p.score == null).length;
    if (missingEntry > 0) errors.push({ severity: 'warning', scope: 'thesis', message: `${missingEntry} thesis thiếu cả entry_price lẫn avg_cost — P&L % không tính được.` });
    if (missingPrice > 0) errors.push({ severity: 'warning', scope: 'thesis', message: `${missingPrice} thesis thiếu giá thị trường hiện tại.` });
    if (!thesis.has_quantity_data) errors.push({ severity: 'info', scope: 'thesis', message: 'Một số thesis chưa có quantity — thị giá & vốn có thể không đầy đủ.' });
    if (missingScore > 0) errors.push({ severity: 'info', scope: 'thesis', message: `${missingScore} thesis chưa có điểm AI score.` });
  }

  return errors;
}

// ---------------------------------------------------------------------------
// Error Banner HTML
// ---------------------------------------------------------------------------
const SEVERITY_META = {
  critical: { icon: '🔴', label: 'Critical', cls: 'perr-critical' },
  warning:  { icon: '🟡', label: 'Warning',  cls: 'perr-warning'  },
  info:     { icon: '🔵', label: 'Info',      cls: 'perr-info'     },
};

function renderErrorBanner(errors) {
  if (!errors.length) return '';
  const criticalCount = errors.filter(e => e.severity === 'critical').length;
  const warningCount  = errors.filter(e => e.severity === 'warning').length;
  const totalCount    = errors.length;
  const summaryParts  = [];
  if (criticalCount) summaryParts.push(`🔴 ${criticalCount} nghiêm trọng`);
  if (warningCount)  summaryParts.push(`🟡 ${warningCount} cảnh báo`);
  const infoCount = totalCount - criticalCount - warningCount;
  if (infoCount)     summaryParts.push(`🔵 ${infoCount} thông tin`);
  const items = errors.map(e => {
    const meta = SEVERITY_META[e.severity] ?? SEVERITY_META.info;
    return `<li class="perr-item ${meta.cls}">${meta.icon} ${e.message}</li>`;
  }).join('');
  return `
    <div class="perr-banner" role="alert" aria-live="polite">
      <button class="perr-toggle" aria-expanded="false" aria-controls="perrList" type="button">
        <span class="perr-summary">⚠️ ${totalCount} vấn đề dữ liệu — ${summaryParts.join(', ')}</span>
        <span class="perr-chevron" aria-hidden="true">▾</span>
      </button>
      <ul class="perr-list" id="perrList" hidden>${items}</ul>
    </div>`;
}

function wireBannerToggle(wrap) {
  const btn  = wrap.querySelector('.perr-toggle');
  const list = wrap.querySelector('.perr-list');
  if (!btn || !list) return;
  btn.addEventListener('click', () => {
    const expanded = btn.getAttribute('aria-expanded') === 'true';
    btn.setAttribute('aria-expanded', String(!expanded));
    list.hidden = expanded;
    btn.querySelector('.perr-chevron').textContent = expanded ? '▾' : '▴';
  });
}

function badgeHTML(count) {
  if (!count) return '';
  return `<span class="perr-tab-badge" aria-label="${count} vấn đề">${count}</span>`;
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

    // Gap 3 B3: warn when linked thesis is no longer active
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
  return `
    ${banner}
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
  if (!data) return '<p class="empty-state">Không thể tải dữ liệu thesis portfolio.</p>';
  const positions = data.positions ?? [];
  if (!positions.length) return '<p class="empty-state">Chưa có thesis active nào. Dùng <code>/thesis add</code> hoặc tạo trực tiếp tại đây.</p>';

  const totalPnlPct = data.total_pnl_pct;
  const winning     = data.winning_count ?? 0;
  const losing      = data.losing_count ?? 0;
  const n           = data.position_count ?? positions.length;

  const missingEntryTickers = new Set(
    positions.filter(p => p.entry_price == null && p.avg_cost == null).map(p => p.ticker),
  );

  const rows = positions.map(p => {
    const verdict    = (p.last_verdict ?? '').toUpperCase();
    const badge      = VERDICT_BADGE[verdict] ?? { icon: '❓', cls: 'badge-unknown' };
    const pnlPct     = p.pnl_pct ?? null;
    const score      = p.score ?? null;
    const tier       = p.score_tier_icon ?? '';
    const entryDisplay = p.avg_cost ?? p.entry_price;
    const entryLabel   = p.avg_cost != null ? 'avg_cost' : 'entry';
    const hasError     = missingEntryTickers.has(p.ticker);
    const thesisAttr   = p.id ? ` data-thesis-id="${p.id}"` : '';

    return `
      <tr class="${hasError ? 'row-data-error' : ''}" data-ticker="${p.ticker}"${thesisAttr}>
        <td class="col-ticker col-center">
          <strong>${p.ticker}</strong>${hasError ? ' <span class="cell-error-dot" title="Thiếu entry & avg_cost">●</span>' : ''}
        </td>
        <td class="col-action col-center"></td>
        <td class="col-center"><span class="verdict-badge ${badge.cls}">${badge.icon} ${verdict || '—'}</span></td>
        <td class="num${entryDisplay == null ? ' cell-missing' : ''}" title="${entryLabel}">${fmtVnd(entryDisplay)}</td>
        <td class="num${p.current_price == null ? ' cell-missing' : ''}">${fmtVnd(p.current_price)}</td>
        <td class="num ${pnlClass(pnlPct)}">${pnlPct != null ? `${pnlIcon(pnlPct)} ${fmtPct(pnlPct)}` : '⚪ —'}</td>
        <td class="num">${score != null ? `${tier} ${score}` : '—'}</td>
      </tr>`;
  }).join('');

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
  // ── Snapshot active tab TRƯỚC khi overwrite innerHTML ─────────────────────
  // Khi loadPortfolio() được gọi lại sau QuickTrade B/S, user có thể đang ở
  // Thesis tab. Nếu không snapshot, innerHTML reset sẽ luôn activate Trades tab.
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

  // ── Restore previously active tab (Wave 3) ────────────────────────────────
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

  // ── Wire error banner toggles ─────────────────────────────────────────────
  wrap.querySelectorAll('.perr-banner').forEach(wireBannerToggle);

  // ── Inject B/S buttons vào CẢ HAI tbody (kể cả pane hidden) ──────────────
  _injectAllTradeButtons(wrap);

  // ── Wire tab switching ────────────────────────────────────────────────────
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
