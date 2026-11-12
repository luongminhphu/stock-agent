/**
 * board.js — Tab "Danh mục & Watchlist": mã đang nắm + mã theo dõi, kèm tiền lệ.
 * Owner: modules/board
 *
 * Nguồn: GET /api/v1/board → { as_of, summary, rows[] }
 *   rows đã sắp xếp sẵn: mã đang nắm theo tỉ trọng, rồi watchlist theo lợi thế.
 *   win_prob_* / base_win_120 / edge_120 là tỉ lệ 0..1; pnl_pct / weight là %.
 *
 * Cột "Kế hoạch": đèn của kế hoạch nắm giữ (modules/plans) cho mã đang nắm;
 *   vẽ lại khi plans:loaded.
 *
 * Exports:
 *   loadBoard({ silent })  — fetch + render vào #boardRoot
 *   initBoard()            — bind sự kiện uỷ quyền (gọi 1 lần)
 */

import { getJson } from '../../api/client.js?v=1';
import { esc } from '../../utils/format.js?v=1';
import { openModal } from '../../utils/dom.js?v=1';
import {
  fmtVnd, fmtPct, fmtProb, fmtPp, fmtNum, signClass, fmtDateVi,
  suggestionBadge, confidenceLabel, openBaserateDrawer,
} from './baserate-drawer.js?v=2';
import { getPlanLights, LIGHTS } from '../plans/plans.js?v=1';

const BOARD_URL = '/api/v1/board';

let _rows = [];
let _seq = 0;
let _bound = false;
let _summary = null;

function _renderTable() {
  const root = _root();
  if (!root) return;
  root.innerHTML = _summaryHTML(_summary?.summary, _rows, _summary?.asOf) + _tableHTML(_rows);
}

function _root() {
  return document.getElementById('boardRoot');
}

// ── Loading / error / empty ─────────────────────────────────────────────────
function _skeletonHTML() {
  const line = (w) => `<span class="bd-skel" style="width:${w}"></span>`;
  return `
    <div class="bd-summary" aria-hidden="true">
      ${[1, 2, 3].map(() => `<div class="bd-stat">${line('60%')}${line('40%')}</div>`).join('')}
    </div>
    <div class="bd-table-wrap" aria-busy="true">
      <p class="bd-loading-text">Đang tính base rate cho từng mã…</p>
      ${[1, 2, 3, 4].map(() => `<div class="bd-skel-row">${line('100%')}</div>`).join('')}
    </div>`;
}

function _errorHTML(msg) {
  return `
    <div class="bd-state bd-state--error" role="alert">
      <p class="bd-state-title">Không tải được bảng Portfolio.</p>
      <p class="bd-state-detail">${esc(msg)}</p>
      <button type="button" class="ghost-btn" data-board-retry>Thử lại</button>
    </div>`;
}

function _emptyHTML() {
  return `
    <div class="bd-state">
      <p class="bd-state-title">Chưa có vị thế hay mã theo dõi — thêm mã vào Watchlist</p>
      <p class="bd-state-detail">Mỗi mã sẽ được so với các lần trước đây ở cùng trạng thái để ra xác suất lãi sau 3 và 6 tháng.</p>
      <button type="button" class="primary-btn" data-board-add>+ Thêm mã vào Watchlist</button>
    </div>`;
}

// ── Summary strip ───────────────────────────────────────────────────────────
function _unrealized(rows) {
  let pnl = 0;
  let cost = 0;
  let n = 0;
  for (const r of rows) {
    if (!r.in_portfolio || r.qty == null || r.avg_cost == null || r.price == null) continue;
    pnl += Number(r.qty) * (Number(r.price) - Number(r.avg_cost));
    cost += Number(r.qty) * Number(r.avg_cost);
    n++;
  }
  return n ? { pnl, pct: cost ? (pnl / cost) * 100 : null } : null;
}

function _chips(list, cls) {
  return list.map(t =>
    `<button type="button" class="bd-chip ${cls}" data-board-ticker="${esc(t)}" aria-label="Xem base rate ${esc(t)}">${esc(t)}</button>`,
  ).join('');
}

function _summaryHTML(summary, rows, asOf) {
  const s = summary ?? {};
  const u = _unrealized(rows);
  const groups = [
    { key: 'lean_avoid_holdings', label: 'Đang nắm – Bearish', cls: 'bd-chip--avoid' },
    { key: 'abnormal_drawdowns',  label: 'Drawdown sâu hơn base rate',      cls: 'bd-chip--warn' },
    { key: 'lean_buy_watchlist',  label: 'Watchlist – Bullish',    cls: 'bd-chip--buy' },
  ];
  const groupHTML = groups
    .filter(g => Array.isArray(s[g.key]) && s[g.key].length)
    .map(g => `
      <div class="bd-attn-group">
        <span class="bd-attn-label">${g.label}</span>
        <span class="bd-attn-chips">${_chips(s[g.key], g.cls)}</span>
      </div>`).join('');
  const insufficient = Array.isArray(s.insufficient) && s.insufficient.length
    ? `<div class="bd-attn-group bd-attn-group--muted">
         <span class="bd-attn-label">Thiếu base rate</span>
         <span class="bd-attn-chips">${_chips(s.insufficient, 'bd-chip--muted')}</span>
       </div>`
    : '';

  return `
    <div class="bd-summary">
      <div class="bd-stat">
        <span class="bd-stat-label">Giá trị Portfolio</span>
        <strong class="bd-stat-value">${s.holdings ? fmtVnd(s.portfolio_value) : '—'}</strong>
        <span class="bd-stat-sub">${fmtNum(s.holdings ?? 0)} mã đang nắm · ${fmtNum(s.watchlist_only ?? 0)} mã theo dõi</span>
      </div>
      <div class="bd-stat">
        <span class="bd-stat-label">Unrealized P&L</span>
        <strong class="bd-stat-value ${u ? signClass(u.pnl) : ''}">${u ? `${u.pnl > 0 ? '+' : ''}${fmtVnd(u.pnl)}` : '—'}</strong>
        <span class="bd-stat-sub ${u ? signClass(u.pct) : ''}">${u && u.pct != null ? `${u.pct > 0 ? '+' : ''}${fmtPct(u.pct)} so với giá vốn` : 'theo giá đóng cửa gần nhất'}</span>
      </div>
      <div class="bd-stat">
        <span class="bd-stat-label">Win rate 6 tháng (theo weight)</span>
        <strong class="bd-stat-value">${fmtProb(s.weighted_win_prob_120)}</strong>
        <span class="bd-stat-sub">base rate 120 phiên, trọng số theo giá trị</span>
      </div>
    </div>
    <div class="bd-attn" aria-label="Mã cần chú ý">
      ${groupHTML || '<p class="bd-attn-none">Không có mã nào lệch khỏi tiền lệ cần chú ý.</p>'}
      ${insufficient}
    </div>
    ${asOf ? `<p class="bd-asof">Base rate tính đến phiên ${fmtDateVi(asOf)} · bấm vào một dòng để xem chi tiết.</p>` : ''}`;
}

// ── Table ───────────────────────────────────────────────────────────────────
function _stateCell(state) {
  const vals = state && typeof state === 'object' ? Object.values(state) : [];
  if (!vals.length) return '<span class="bd-muted">—</span>';
  const short = vals.slice(0, 2).map(esc).join(' · ');
  const full = vals.map(esc).join('\n');
  return `<span class="bd-state-text" title="${full}">${short}${vals.length > 2 ? ` <span class="bd-muted">+${vals.length - 2}</span>` : ''}</span>`;
}

function _planCell(r) {
  const p = getPlanLights().get(String(r.ticker).toUpperCase());
  if (p) {
    const l = LIGHTS[p.light] ? p.light : 'xam';
    return `<span class="bd-plan-dot" title="Kế hoạch: ${LIGHTS[l].label}"><span class="pl-light pl-light--${l}" aria-hidden="true"></span><span class="sr-only">${LIGHTS[l].label}</span></span>`;
  }
  if (!r.in_portfolio) return '<span class="bd-muted">—</span>';
  return `<button type="button" class="bd-plan-none" data-plan-new="${esc(r.ticker)}" title="Lập kế hoạch nắm giữ ${esc(r.ticker)}">+ Lập</button>`;
}

function _rowHTML(r) {
  const t = esc(r.ticker);
  const tag = r.in_portfolio
    ? '<span class="bd-tag">Đang nắm</span>'
    : '<span class="bd-tag bd-tag--watch">Watchlist</span>';
  const tickerCell = `
    <th scope="row" class="bd-col-ticker">
      <button type="button" class="bd-ticker" data-board-ticker="${t}" aria-label="Xem base rate của ${t}">${t}</button>
      ${tag}
    </th>`;

  if (r.error) {
    return `<tr class="bd-row bd-row--error" data-ticker="${t}">
      ${tickerCell}
      <td class="num">${fmtVnd(r.price)}</td>
      <td colspan="9" class="bd-muted">Không lấy được dữ liệu</td>
    </tr>`;
  }

  const flag = r.drawdown_flag === 'sau_hon_tien_le'
    ? `<span class="bd-flag" title="Mức lỗ hiện tại sâu hơn avg max drawdown của base rate (${fmtPct((r.typical_drawdown ?? 0) * 100)})">
         <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
         Drawdown sâu hơn base rate
       </span>`
    : '';

  const pnl = r.in_portfolio && r.pnl_pct != null
    ? `<span class="${signClass(r.pnl_pct)}">${r.pnl_pct > 0 ? '+' : ''}${fmtPct(r.pnl_pct)}</span>`
    : '<span class="bd-muted">—</span>';

  const base = r.base_win_120 != null ? `<span class="bd-sub">base ${fmtProb(r.base_win_120, 0)}</span>` : '';

  return `<tr class="bd-row" data-ticker="${t}" data-in-portfolio="${r.in_portfolio ? '1' : '0'}">
    ${tickerCell}
    <td class="num">${fmtVnd(r.price)}</td>
    <td class="num">${pnl}${flag}</td>
    <td class="num">${r.in_portfolio && r.weight != null ? fmtPct(r.weight) : '<span class="bd-muted">—</span>'}</td>
    <td class="bd-col-state">${_stateCell(r.state)}</td>
    <td class="num">${fmtProb(r.win_prob_60)}</td>
    <td class="num"><span class="bd-strong">${fmtProb(r.win_prob_120)}</span>${base}</td>
    <td class="num ${signClass(r.edge_120)}">${fmtPp(r.edge_120)}</td>
    <td class="num"><span>${confidenceLabel(r.confidence)}</span><span class="bd-sub">n=${fmtNum(r.episodes ?? 0)}</span></td>
    <td class="bd-col-sg">${suggestionBadge(r.suggestion)}</td>
    <td class="bd-col-plan">${_planCell(r)}</td>
  </tr>`;
}

function _tableHTML(rows) {
  return `
    <div class="bd-table-wrap tbl-scroll" tabindex="0" role="region" aria-label="Bảng danh mục và watchlist (cuộn ngang)">
      <table class="bd-table">
        <thead>
          <tr>
            <th scope="col" class="bd-col-ticker">Mã</th>
            <th scope="col" class="num">Giá</th>
            <th scope="col" class="num">P&amp;L</th>
            <th scope="col" class="num">Weight</th>
            <th scope="col">Trạng thái</th>
            <th scope="col" class="num" title="Win rate: xác suất lãi vượt chi phí sau 60 phiên">Win 3T</th>
            <th scope="col" class="num" title="Win rate sau 120 phiên, kèm base rate của mã">Win 6T</th>
            <th scope="col" class="num" title="Edge = Win 6T − base rate, tính bằng điểm phần trăm">Edge</th>
            <th scope="col" class="num" title="Confidence theo số episode độc lập">Confidence</th>
            <th scope="col" title="Bullish / Bearish / Neutral theo base rate của chính mã — không phải dự báo">Signal</th>
            <th scope="col" class="bd-col-plan" title="Đèn của kế hoạch nắm giữ — xem tab Kế hoạch">Plan</th>
          </tr>
        </thead>
        <tbody>${rows.map(_rowHTML).join('')}</tbody>
      </table>
    </div>`;
}

// ── Public ──────────────────────────────────────────────────────────────────
export async function loadBoard({ silent = false } = {}) {
  const root = _root();
  if (!root) return;
  initBoard();
  const seq = ++_seq;
  if (!silent || !_rows.length) root.innerHTML = _skeletonHTML();
  try {
    const data = await getJson(BOARD_URL);
    if (seq !== _seq) return;
    const rows = Array.isArray(data?.rows) ? data.rows : [];
    _rows = rows;
    if (!rows.length) {
      root.innerHTML = _emptyHTML();
      return;
    }
    _summary = { summary: data.summary, asOf: data.as_of };
    _renderTable();
    document.dispatchEvent(new CustomEvent('board:rendered', { detail: { count: rows.length } }));
  } catch (err) {
    if (seq !== _seq) return;
    if (silent && _rows.length) {
      console.warn('[board] refresh failed:', err?.message);
      return;
    }
    root.innerHTML = _errorHTML(err?.message ?? 'Lỗi không xác định');
  }
}

export function initBoard() {
  if (_bound) return;
  const root = _root();
  if (!root) return;
  _bound = true;

  document.addEventListener('plans:loaded', () => {
    if (_rows.length && root.querySelector('.bd-table')) _renderTable();
  });

  root.addEventListener('click', (e) => {
    if (e.target.closest('[data-plan-new]')) return; // plans.js mở form
    if (e.target.closest('[data-board-retry]')) { loadBoard(); return; }
    if (e.target.closest('[data-board-add]')) { openModal('watchlistAddModal'); return; }
    const btn = e.target.closest('[data-board-ticker]');
    const row = btn ? null : e.target.closest('tr.bd-row');
    const ticker = btn?.dataset.boardTicker ?? row?.dataset.ticker;
    if (!ticker) return;
    const info = _rows.find(r => String(r.ticker).toUpperCase() === ticker.toUpperCase());
    openBaserateDrawer(ticker, { inPortfolio: !!info?.in_portfolio });
  });
}
