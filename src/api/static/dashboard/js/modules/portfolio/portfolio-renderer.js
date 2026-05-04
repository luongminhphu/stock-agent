/**
 * portfolio-renderer.js
 * Owner: modules/portfolio
 * Responsibility: build HTML cho portfolio section (2 tabs: Trades / Thesis).
 * Rule: KHÔNG fetch, KHÔNG gọi API. Chỉ nhận data → trả HTML string.
 */

import { el } from '../../utils/dom.js';

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------
function fmtVnd(val) {
  if (val == null || isNaN(val)) return '—';
  return new Intl.NumberFormat('vi-VN', { style: 'currency', currency: 'VND', maximumFractionDigits: 0 }).format(val);
}

/**
 * Format tỷ lệ phần trăm.
 * Nhận vào dạng decimal (0.6594 = 65.94%) — nhân 100 bên trong.
 * Trades: truyền unrealized_pct / 100  (vì backend trả dạng %, ví dụ 65.94)
 * Thesis: truyền pnl_pct / 100         (backend trả dạng %, ví dụ 65.94)
 */
function fmtPct(val) {
  if (val == null || isNaN(val)) return '—';
  const sign = val >= 0 ? '+' : '';
  return `${sign}${(val * 100).toFixed(2)}%`;
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
// Trades tab renderer
// ---------------------------------------------------------------------------
function renderTradesTab(data) {
  if (!data) return '<p class="empty-state">Không thể tải dữ liệu giao dịch.</p>';

  const positions = data.positions ?? [];
  if (!positions.length) {
    return '<p class="empty-state">Chưa có vị thế nào. Dùng <code>/buy</code> trên Discord để bắt đầu.</p>';
  }

  const totalPnl     = data.total_unrealized_pnl ?? 0;
  const totalPct     = data.total_unrealized_pct ?? 0;   // dạng % (vd: 18.63)
  const totalCost    = data.total_cost_basis ?? 0;
  const totalMkt     = data.total_market_value ?? 0;

  const rows = positions.map(p => {
    // unrealized_pct từ PnlService là dạng % thực (vd: -2.825, 65.94)
    // fmtPct() nhận decimal nên cần chia 100
    const pct = p.unrealized_pct != null ? p.unrealized_pct / 100 : null;
    return `
      <tr>
        <td><strong>${p.ticker}</strong></td>
        <td class="num">${p.qty != null ? p.qty.toLocaleString('vi-VN') : '—'}</td>
        <td class="num">${fmtVnd(p.avg_cost)}</td>
        <td class="num">${fmtVnd(p.current_price)}</td>
        <td class="num">${fmtVnd(p.cost_basis)}</td>
        <td class="num">${fmtVnd(p.market_value)}</td>
        <td class="num ${pnlClass(p.unrealized_pnl)}">
          ${pnlIcon(p.unrealized_pnl)} ${fmtVnd(p.unrealized_pnl)}
        </td>
        <td class="num ${pnlClass(pct)}">${fmtPct(pct)}</td>
      </tr>`;
  }).join('');

  return `
    <div class="portfolio-summary">
      <span class="summary-chip">${pnlIcon(totalPnl)} P&amp;L: <strong class="${pnlClass(totalPnl)}">${fmtVnd(totalPnl)}</strong> (${fmtPct(totalPct / 100)})</span>
      <span class="summary-chip">Vốn: <strong>${fmtVnd(totalCost)}</strong></span>
      <span class="summary-chip">Thị giá: <strong>${fmtVnd(totalMkt)}</strong></span>
      <span class="summary-chip">Vị thế: <strong>${positions.length}</strong></span>
    </div>
    <div class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th>Ticker</th><th class="num">Số cổ</th><th class="num">Giá vốn TB</th>
            <th class="num">Giá HT</th><th class="num">Chi phí vốn</th>
            <th class="num">Thị giá</th><th class="num">P&amp;L</th><th class="num">%</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
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

function renderThesisTab(data) {
  if (!data) return '<p class="empty-state">Không thể tải dữ liệu thesis portfolio.</p>';

  const positions = data.positions ?? [];
  if (!positions.length) {
    return '<p class="empty-state">Chưa có thesis active nào. Dùng <code>/thesis add</code> hoặc tạo trực tiếp tại đây.</p>';
  }

  const totalPnlPct = data.total_pnl_pct;
  const winning     = data.winning_count ?? 0;
  const losing      = data.losing_count ?? 0;
  const n           = data.position_count ?? positions.length;

  const rows = positions.map(p => {
    const verdict = (p.last_verdict ?? '').toUpperCase();
    const badge   = VERDICT_BADGE[verdict] ?? { icon: '❓', cls: 'badge-unknown' };
    const pnlPct  = p.pnl_pct != null ? p.pnl_pct / 100 : null;   // API trả %, cần /100 cho fmtPct
    const score   = p.score != null ? p.score : null;
    const tier    = p.score_tier_icon ?? '';

    return `
      <tr>
        <td><strong>${p.ticker}</strong></td>
        <td><span class="verdict-badge ${badge.cls}">${badge.icon} ${verdict || '—'}</span></td>
        <td class="num">${fmtVnd(p.entry_price)}</td>
        <td class="num">${fmtVnd(p.current_price)}</td>
        <td class="num ${pnlClass(pnlPct)}">${pnlPct != null ? `${pnlIcon(pnlPct)} ${fmtPct(pnlPct)}` : '⚪ —'}</td>
        <td class="num">${score != null ? `${tier} ${score}` : '—'}</td>
      </tr>`;
  }).join('');

  const summaryPnl = totalPnlPct != null
    ? `${pnlIcon(totalPnlPct)} P&amp;L avg: <strong class="${pnlClass(totalPnlPct)}">${fmtPct(totalPnlPct / 100)}</strong>`
    : '';

  const warningHTML = !data.has_quantity_data
    ? '<p class="muted" style="font-size:.8rem;margin-top:8px">⚠️ Một số thesis chưa có quantity — thị giá/vốn có thể không đầy đủ.</p>'
    : '';

  return `
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
            <th>Ticker</th><th>Verdict</th><th class="num">Entry</th>
            <th class="num">Giá HT</th><th class="num">P&amp;L %</th><th class="num">Score</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    ${warningHTML}`;
}

// ---------------------------------------------------------------------------
// Main render — 2-tab layout
// ---------------------------------------------------------------------------
export function renderPortfolio(wrap, { trades, thesis }) {
  wrap.innerHTML = `
    <div class="portfolio-tab-bar" role="tablist" aria-label="Portfolio view">
      <button class="portfolio-tab active" role="tab" aria-selected="true"
              aria-controls="portfolioTradesPane" id="tabTrades">
        📊 Trades
      </button>
      <button class="portfolio-tab" role="tab" aria-selected="false"
              aria-controls="portfolioThesisPane" id="tabThesis">
        📝 Thesis
      </button>
    </div>
    <div id="portfolioTradesPane" class="portfolio-pane">${renderTradesTab(trades)}</div>
    <div id="portfolioThesisPane" class="portfolio-pane hidden">${renderThesisTab(thesis)}</div>
  `;

  // Wire tab switching
  wrap.querySelectorAll('.portfolio-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      wrap.querySelectorAll('.portfolio-tab').forEach(t => {
        t.classList.remove('active');
        t.setAttribute('aria-selected', 'false');
      });
      wrap.querySelectorAll('.portfolio-pane').forEach(p => p.classList.add('hidden'));

      btn.classList.add('active');
      btn.setAttribute('aria-selected', 'true');
      wrap.querySelector(`#${btn.getAttribute('aria-controls')}`)?.classList.remove('hidden');
    });
  });
}
