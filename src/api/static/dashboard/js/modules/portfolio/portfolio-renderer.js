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
 * Wave 4 — Exposure bar (Gap 2 Option A):
 *   - renderExposureBar(positions) → HTML horizontal stacked bar + legend
 *   - hiển thị sau holdings table, trước error banner
 *   - cảnh báo ⚠️ Over-concentrated khi ticker >= 30% danh mục
 */

// ---------------------------------------------------------------------------
// Format helpers (inline để tránh import)
// ---------------------------------------------------------------------------
const _fmtNum = n => (n == null ? '—' : Number(n).toLocaleString('vi-VN'));
const _fmtPct = p => (p == null ? '—' : (p >= 0 ? '+' : '') + p.toFixed(1) + '%');
const _esc    = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------
export function renderPortfolio(container, { trades, thesis }) {
  // Wave 3: snapshot active tab before overwrite
  const prevActiveTab = container.querySelector('.tab-btn.active')?.dataset?.tab ?? 'trades';

  const tradesRows = _buildTradesRows(trades);
  const thesisRows = _buildThesisRows(thesis);
  const errors     = collectErrors(trades, thesis);

  const tradeErrCount  = tradesRows.filter(r => r.hasError).length;
  const thesisErrCount = thesisRows.filter(r => r.hasError).length;
  const tradeBadge  = tradeErrCount  ? ` <span class="tab-badge">${tradeErrCount}</span>`  : '';
  const thesisBadge = thesisErrCount ? ` <span class="tab-badge">${thesisErrCount}</span>` : '';

  container.innerHTML = `
    ${renderErrorBanner(errors)}
    <div class="tab-bar" role="tablist" aria-label="Portfolio views">
      <button class="tab-btn" role="tab" data-tab="trades"  aria-selected="false">Trades${tradeBadge}</button>
      <button class="tab-btn" role="tab" data-tab="thesis" aria-selected="false">Thesis${thesisBadge}</button>
    </div>
    <div class="tab-pane" data-tab-pane="trades">
      ${_buildTradesTable(tradesRows)}
      ${_renderExposureBar(trades?.positions ?? [])}
    </div>
    <div class="tab-pane" data-tab-pane="thesis">
      ${_buildThesisTable(thesisRows)}
    </div>
  `;

  _bindTabs(container, prevActiveTab);
  _injectTradeButtons(container);
}

// ---------------------------------------------------------------------------
// Tab switching
// ---------------------------------------------------------------------------
function _bindTabs(wrap, activeTab) {
  const btns  = wrap.querySelectorAll('.tab-btn');
  const panes = wrap.querySelectorAll('.tab-pane');

  function activate(tab) {
    btns.forEach(b  => { b.classList.toggle('active', b.dataset.tab === tab); b.setAttribute('aria-selected', b.dataset.tab === tab); });
    panes.forEach(p => p.classList.toggle('active', p.dataset.tabPane === tab));
  }

  btns.forEach(btn => btn.addEventListener('click', () => activate(btn.dataset.tab)));
  activate(activeTab);
}

// ---------------------------------------------------------------------------
// QuickTrade injection
// ---------------------------------------------------------------------------
function _injectTradeButtons(wrap) {
  if (!window.QuickTrade?.injectTradeButtons) return;
  const tradesTbody = wrap.querySelector('[data-holdings-tbody="trades"]');
  const thesisTbody = wrap.querySelector('[data-holdings-tbody="thesis"]');
  if (tradesTbody) window.QuickTrade.injectTradeButtons(tradesTbody, { fromThesisTab: false });
  if (thesisTbody) window.QuickTrade.injectTradeButtons(thesisTbody, { fromThesisTab: true  });
}

// ---------------------------------------------------------------------------
// Trades rows builder  (source: /portfolio/trades  — PnlService)
// ---------------------------------------------------------------------------
function _buildTradesRows(data) {
  if (!data || !Array.isArray(data.positions)) return [];
  return data.positions.map(p => {
    const errors    = [];
    const ticker    = p.ticker       ?? '';
    const qty       = p.qty          ?? p.quantity      ?? null;
    const avgCost   = p.avg_cost     ?? null;
    const currPrice = p.current_price ?? p.curr_price  ?? null;
    const pnlAbs    = p.unrealized_pnl ?? p.pnl_abs    ?? null;
    const pnlPct    = p.unrealized_pct ?? p.pnl_pct    ?? null;
    const thesisId  = p.thesis_id    ?? null;
    const thesisSt  = p.thesis_status ?? null;
    const priceStale = p.price_stale  ?? false;

    if (!ticker)           errors.push('ticker');
    if (qty       == null) errors.push('qty');
    if (avgCost   == null) errors.push('avg_cost');
    if (currPrice == null) errors.push('curr_price');

    const pnlClass = pnlAbs == null ? '' : pnlAbs > 0 ? 'positive' : pnlAbs < 0 ? 'negative' : 'neutral';
    const rowClass = (thesisSt === 'invalidated' || thesisSt === 'closed') ? ' class="row-thesis-warning"' : '';
    const warnBadge = (thesisSt === 'invalidated' || thesisSt === 'closed')
      ? ` <span class="badge-thesis-warning" title="Thesis ${thesisSt}">⚠️</span>` : '';
    const thesisAttr = thesisId ? ` data-thesis-id="${thesisId}"` : '';
    const thesisRef  = thesisId ? `<span class="thesis-tag">#${thesisId}</span>` : '<span class="muted">—</span>';

    return {
      hasError: errors.length > 0,
      html: `<tr data-ticker="${_esc(ticker)}"${thesisAttr}${rowClass}>
        <td class="col-left"><strong>${_esc(ticker)}</strong>${warnBadge}${errors.includes('ticker') ? ' <span class="cell-error" title="Thiếu ticker">⚠</span>' : ''}</td>
        <td class="num">${qty != null ? _fmtNum(qty) : '<span class="cell-error" title="Thiếu qty">⚠</span>'}</td>
        <td class="currency">${avgCost != null ? _fmtNum(avgCost) : '<span class="cell-error" title="Thiếu avg_cost">⚠</span>'}</td>
        <td class="currency">${currPrice != null
          ? `<span class="price-val">${_fmtNum(currPrice)}</span>${priceStale ? '<span class="price-stale-badge" title="Gi\u00e1 cu\u1ed1i phi\u00ean \u2014 ch\u01b0a c\u1eadp nh\u1eadt realtime">Cu\u1ed1i phi\u00ean</span>' : ''}`
          : '<span class="cell-error" title="Thi\u1ebfu curr_price">\u26a0</span>'}</td>
        <td class="currency col-pnl ${pnlClass}">${_fmtNum(pnlAbs)}</td>
        <td class="num col-pct ${pnlClass}">${_fmtPct(pnlPct)}</td>
        <td class="col-center">${thesisRef}</td>
        <td class="col-center col-action"></td>
      </tr>`,
    };
  });
}

// ---------------------------------------------------------------------------
// Thesis rows builder  (source: /portfolio  — DashboardService)
// ---------------------------------------------------------------------------
function _buildThesisRows(data) {
  if (!data || !Array.isArray(data.positions)) return [];
  return data.positions.map(p => {
    const errors    = [];
    const ticker    = p.ticker       ?? '';
    const thesisId  = p.thesis_id    ?? null;
    const qty       = p.quantity     ?? p.qty       ?? null;
    const avgCost   = p.avg_cost     ?? null;
    const currPrice = p.current_price ?? null;
    const pnlAbs    = p.pnl_abs      ?? null;
    const pnlPct    = p.pnl_pct      ?? null;
    const verdict   = p.last_verdict ?? p.verdict   ?? null;
    const score     = p.score        ?? null;

    if (!ticker) errors.push('ticker');

    const pnlClass     = pnlAbs == null ? '' : pnlAbs > 0 ? 'positive' : pnlAbs < 0 ? 'negative' : 'neutral';
    const verdictClass = verdict ? String(verdict).toLowerCase() : '';
    const verdictLabel = verdict
      ? `<span class="badge ${verdictClass}">${_esc(String(verdict).toUpperCase())}</span>`
      : '<span class="muted">—</span>';
    const scoreLabel   = score != null ? `<span class="score-chip">${Math.round(score)}</span>` : '';
    const thesisAttr   = thesisId ? ` data-thesis-id="${thesisId}"` : '';

    return {
      hasError: errors.length > 0,
      html: `<tr data-ticker="${_esc(ticker)}"${thesisAttr}>
        <td class="col-left"><strong>${_esc(ticker)}</strong>${errors.includes('ticker') ? ' <span class="cell-error">⚠</span>' : ''}</td>
        <td class="num">${qty != null ? _fmtNum(qty) : '<span class="muted">—</span>'}</td>
        <td class="currency">${avgCost != null ? _fmtNum(avgCost) : '<span class="muted">—</span>'}</td>
        <td class="currency">${currPrice != null ? _fmtNum(currPrice) : '<span class="muted">—</span>'}</td>
        <td class="currency col-pnl ${pnlClass}">${_fmtNum(pnlAbs)}</td>
        <td class="num col-pct ${pnlClass}">${_fmtPct(pnlPct)}</td>
        <td class="col-center">${verdictLabel} ${scoreLabel}</td>
        <td class="col-center col-action"></td>
      </tr>`,
    };
  });
}

// ---------------------------------------------------------------------------
// Table builders
// ---------------------------------------------------------------------------
function _buildTradesTable(rows) {
  const body = rows.length
    ? rows.map(r => r.html).join('')
    : '<tr><td colspan="8" class="empty-state">Chưa có vị thế nào.</td></tr>';
  return `<div class="portfolio-pane"><div class="table-scroll">
    <table class="data-table">
      <thead><tr>
        <th class="col-left">Ticker</th>
        <th class="num">SL (cp)</th>
        <th class="currency">Giá vốn</th>
        <th class="currency">Thị giá</th>
        <th class="currency">P&amp;L (₫)</th>
        <th class="num">P&amp;L (%)</th>
        <th class="col-center">Thesis</th>
        <th class="col-center"></th>
      </tr></thead>
      <tbody data-holdings-tbody="trades">${body}</tbody>
    </table>
  </div></div>`;
}

function _buildThesisTable(rows) {
  const body = rows.length
    ? rows.map(r => r.html).join('')
    : '<tr><td colspan="8" class="empty-state">Chưa có vị thế nào.</td></tr>';
  return `<div class="portfolio-pane"><div class="table-scroll">
    <table class="data-table">
      <thead><tr>
        <th class="col-left">Ticker</th>
        <th class="num">SL (cp)</th>
        <th class="currency">Giá vốn</th>
        <th class="currency">Thị giá</th>
        <th class="currency">P&amp;L (₫)</th>
        <th class="num">P&amp;L (%)</th>
        <th class="col-center">Verdict</th>
        <th class="col-center"></th>
      </tr></thead>
      <tbody data-holdings-tbody="thesis">${body}</tbody>
    </table>
  </div></div>`;
}

// ---------------------------------------------------------------------------
// Exposure bar (Gap 2 Option A)
// ---------------------------------------------------------------------------
function _renderExposureBar(positions) {
  const withMV = (positions ?? []).filter(p => p.market_value != null && p.market_value > 0);
  if (withMV.length === 0) return '';

  const total   = withMV.reduce((s, p) => s + p.market_value, 0);
  const sorted  = [...withMV].sort((a, b) => b.market_value - a.market_value);
  const palette = ['#4f98a3','#d19900','#6daa45','#bb653b','#5591c7','#a86fdf','#dd6974','#fdab43','#6daa45','#4f98a3'];

  const warnChip = sorted.some(p => (p.market_value / total) >= 0.3)
    ? '<span class="exposure-warn-chip" title="1 mã chiếm ≥30% danh mục">⚠️ Over-concentrated</span>'
    : '';

  const segments = sorted.map((p, i) => {
    const pct = (p.market_value / total * 100).toFixed(1);
    return `<div class="exposure-segment" style="width:${pct}%;background:${palette[i % palette.length]}" title="${_esc(p.ticker)} ${pct}%"></div>`;
  }).join('');

  const legend = sorted.map((p, i) => {
    const pct  = (p.market_value / total * 100).toFixed(1);
    const warn = (p.market_value / total) >= 0.3 ? ' exposure-legend-warn' : '';
    return `<span class="exposure-legend-item${warn}">
      <span class="exposure-dot" style="background:${palette[i % palette.length]}"></span>
      <span class="exposure-ticker">${_esc(p.ticker)}</span>
      <span class="exposure-pct">${pct}%</span>
    </span>`;
  }).join('');

  return `<div class="exposure-bar-block">
    <div class="exposure-bar-header">
      <span class="exposure-label">Concentration</span>
      ${warnChip}
    </div>
    <div class="exposure-bar-track">${segments}</div>
    <div class="exposure-legend">${legend}</div>
  </div>`;
}

// ---------------------------------------------------------------------------
// Error collection & banner
// ---------------------------------------------------------------------------
export function collectErrors(trades, thesis) {
  const errors = [];

  if (!trades) {
    errors.push({ severity: 'error', scope: 'trades', message: 'Không tải được dữ liệu Trades.' });
  } else if (trades.errors?.length) {
    trades.errors.forEach(e => errors.push({ severity: 'warning', scope: 'trades', message: e }));
  }

  if (!thesis) {
    errors.push({ severity: 'error', scope: 'thesis', message: 'Không tải được dữ liệu Thesis.' });
  } else if (!thesis.positions?.length && !thesis.total_cost_basis) {
    // silently empty — not an error
  }

  return errors;
}

export function renderErrorBanner(errors) {
  if (!errors?.length) return '';
  const hasError   = errors.some(e => e.severity === 'error');
  const severityClass = hasError ? 'banner-error' : 'banner-warning';
  const rows = errors.map(e =>
    `<li><span class="badge-scope">${_esc(e.scope)}</span> ${_esc(e.message)}</li>`
  ).join('');
  return `<details class="error-banner ${severityClass}" open>
    <summary>${hasError ? '❌' : '⚠️'} ${errors.length} vấn đề dữ liệu</summary>
    <ul>${rows}</ul>
  </details>`;
}
