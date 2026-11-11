/**
 * baserate-drawer.js — Renderer "tiền lệ" dùng chung + drawer chi tiết từng mã.
 * Owner: modules/board
 *
 * Nguồn dữ liệu:
 *   GET /api/v1/market/baserate/{ticker}   (cổ phiếu, kỳ 60/120 phiên)
 *   GET /api/v1/derivatives/vn30/baserate  (VN30, kỳ 5/20/60 phiên) — cùng shape
 *
 * Exports:
 *   fmtNum, fmtVnd, fmtPct, fmtProb, fmtPp, fmtSignedPct, signClass, fmtDateVi
 *   SUGGESTION, CONFIDENCE, suggestionBadge(code), confidenceLabel(code)
 *   renderBaserate(data, opts) → HTML string (không tự fetch)
 *   openBaserateDrawer(ticker, opts) — mở drawer, fetch + render, focus trap, Esc đóng
 *
 * Mọi chuỗi từ API đều qua esc() trước khi chèn vào HTML.
 */

import { getJson, marketApiBase } from '../../api/client.js?v=1';
import { esc } from '../../utils/format.js?v=1';

// ── Định dạng số (vi-VN) ─────────────────────────────────────────────────────
const _nfCache = new Map();
function _nf(min, max) {
  const key = `${min}:${max}`;
  if (!_nfCache.has(key)) {
    _nfCache.set(key, new Intl.NumberFormat('vi-VN', {
      minimumFractionDigits: min,
      maximumFractionDigits: max,
    }));
  }
  return _nfCache.get(key);
}

const DASH = '—';
const _isNum = (v) => v != null && v !== '' && Number.isFinite(Number(v));

/** Số với đúng `decimals` chữ số thập phân. */
export function fmtNum(v, decimals = 0) {
  return _isNum(v) ? _nf(decimals, decimals).format(Number(v)) : DASH;
}

/** Tiền VND, không thập phân. */
export function fmtVnd(v) {
  return _isNum(v) ? _nf(0, 0).format(Math.round(Number(v))) : DASH;
}

/** Giá trị đã là phần trăm (vd 12.5) → "12,5%". */
export function fmtPct(v, decimals = 1) {
  return _isNum(v) ? `${_nf(decimals, decimals).format(Number(v))}%` : DASH;
}

/** Tỉ lệ 0..1 → "48,0%". */
export function fmtProb(v, decimals = 1) {
  return _isNum(v) ? fmtPct(Number(v) * 100, decimals) : DASH;
}

/** Tỉ lệ 0..1 → có dấu "+12,0%". */
export function fmtSignedPct(frac, decimals = 1) {
  if (!_isNum(frac)) return DASH;
  const v = Number(frac) * 100;
  return `${v > 0 ? '+' : ''}${fmtPct(v, decimals)}`;
}

/** Hiệu xác suất 0..1 → điểm phần trăm "+12,0 đ%". */
export function fmtPp(frac, decimals = 1) {
  if (!_isNum(frac)) return DASH;
  const v = Number(frac) * 100;
  return `${v > 0 ? '+' : ''}${_nf(decimals, decimals).format(v)} đ%`;
}

/** Lớp màu tăng/giảm theo dấu (tăng xanh, giảm đỏ). */
export function signClass(v) {
  if (!_isNum(v) || Number(v) === 0) return '';
  return Number(v) > 0 ? 'is-up' : 'is-down';
}

export function fmtDateVi(d) {
  if (!d) return DASH;
  const s = typeof d === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(d) ? `${d}T00:00` : d;
  const dt = new Date(s);
  if (Number.isNaN(dt.getTime())) return esc(d);
  return dt.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

// ── Từ điển hiển thị ─────────────────────────────────────────────────────────
export const SUGGESTION = Object.freeze({
  NGHIENG_MUA:      { label: 'Nghiêng mua',      cls: 'sg-buy' },
  NGHIENG_TRANH:    { label: 'Nghiêng tránh',    cls: 'sg-avoid' },
  TRUNG_TINH:       { label: 'Trung tính',       cls: 'sg-neutral' },
  KHONG_DU_TIEN_LE: { label: 'Không đủ base rate', cls: 'sg-insufficient' },
});

export const CONFIDENCE = Object.freeze({
  khong_du:   'Không đủ',
  thap:       'Thấp',
  trung_binh: 'TB',
  cao:        'Cao',
});

const CONFIDENCE_LONG = Object.freeze({
  khong_du:   'Không đủ',
  thap:       'Thấp',
  trung_binh: 'Trung bình',
  cao:        'Cao',
});

/** Tên đặc trưng trạng thái (khóa của matched_on / dropped). */
const FEATURE_NAMES = Object.freeze({
  trend:       'xu hướng MA50/MA200',
  range_52w:   'vị trí trong biên độ 52 tuần',
  momentum_3m: 'đà 3 tháng',
  volume:      'khối lượng',
  valuation:   'định giá P/E',
  earnings:    'tăng trưởng EPS',
});

const HORIZON_HINT = Object.freeze({ 5: '~1 tuần', 20: '~1 tháng', 60: '~3 tháng', 120: '~6 tháng' });

export function suggestionBadge(code) {
  const s = SUGGESTION[code] ?? SUGGESTION.KHONG_DU_TIEN_LE;
  return `<span class="sg-badge ${s.cls}">${s.label}</span>`;
}

export function confidenceLabel(code, long = false) {
  const map = long ? CONFIDENCE_LONG : CONFIDENCE;
  return map[code] ?? esc(code ?? DASH);
}

function _featureName(key) {
  return FEATURE_NAMES[key] ?? esc(key);
}

// ── Renderer dùng chung ──────────────────────────────────────────────────────
/**
 * @param {object} d   BaseRateResponse
 * @param {{ priceDecimals?: number, compact?: boolean, maxAnalogs?: number }} [opts]
 * @returns {string} HTML
 */
export function renderBaserate(d, opts = {}) {
  if (!d || typeof d !== 'object') {
    return '<p class="br-empty">Không có dữ liệu base rate.</p>';
  }
  const priceDecimals = opts.priceDecimals ?? 0;
  const compact = !!opts.compact;
  const outcomes = (Array.isArray(d.outcomes) ? d.outcomes : [])
    .slice()
    .sort((a, b) => (a.horizon ?? 0) - (b.horizon ?? 0));
  const horizons = outcomes.map(o => o.horizon);
  const nList = horizons.length ? horizons.join('/') : 'N';

  const labels = d.state_labels && typeof d.state_labels === 'object' ? d.state_labels : {};
  const matched = new Set(Array.isArray(d.matched_on) ? d.matched_on : []);
  const dropped = Array.isArray(d.dropped) ? d.dropped : [];
  const stateItems = Object.entries(labels).map(([k, v]) => {
    const relaxed = dropped.includes(k) || (matched.size && !matched.has(k));
    return `<li class="${relaxed ? 'is-relaxed' : ''}">${esc(v)}${relaxed ? ' <span class="br-tag-muted">(đã nới)</span>' : ''}</li>`;
  }).join('');

  const head = `
    <div class="br-head">
      ${suggestionBadge(d.suggestion)}
      <span class="br-meta">Confidence: <strong>${confidenceLabel(d.confidence, true)}</strong></span>
      ${_isNum(d.matching_days) ? `<span class="br-meta">${fmtNum(d.matching_days)} phiên cùng trạng thái</span>` : ''}
      ${d.history_from ? `<span class="br-meta">Lịch sử từ ${fmtDateVi(d.history_from)}</span>` : ''}
    </div>
    <p class="br-explainer">Xác suất = tỉ lệ các lần trước đây mã ở cùng trạng thái có lãi vượt chi phí 0,4% sau ${esc(nList)} phiên, đã co về base rate của chính mã. Không phải dự báo.</p>`;

  const state = stateItems ? `
    <section class="br-section" aria-label="Trạng thái hiện tại">
      <h3 class="br-h">Trạng thái hiện tại${d.as_of ? ` <span class="br-h-sub">ngày ${fmtDateVi(d.as_of)}</span>` : ''}</h3>
      <ul class="br-states">${stateItems}</ul>
      ${dropped.length ? `<p class="br-note">Đã nới điều kiện: ${dropped.map(_featureName).join(', ')} — không đủ tiền lệ khi khớp đủ mọi đặc trưng.</p>` : ''}
    </section>` : '';

  const cards = outcomes.length ? outcomes.map(o => {
    const hint = HORIZON_HINT[o.horizon] ? ` (${HORIZON_HINT[o.horizon]})` : '';
    return `
      <article class="br-outcome">
        <h4 class="br-outcome-title">Sau ${esc(o.horizon)} phiên${hint}</h4>
        <div class="br-prob">
          <span class="br-prob-val">${fmtProb(o.win_prob)}</span>
          <span class="br-prob-sub">win rate · khoảng ${fmtProb(o.prob_low, 0)}–${fmtProb(o.prob_high, 0)}</span>
        </div>
        <dl class="br-dl">
          <div><dt>Base rate của mã</dt><dd>${fmtProb(o.base_win_rate)}</dd></div>
          <div><dt>Edge</dt><dd class="${signClass(o.edge)}">${fmtPp(o.edge)}</dd></div>
          <div><dt>Median return</dt><dd class="${signClass(o.median_return)}">${fmtSignedPct(o.median_return)}</dd></div>
          <div><dt>Khoảng P10 – P90</dt><dd>${fmtSignedPct(o.p10_return)} … ${fmtSignedPct(o.p90_return)}</dd></div>
          <div><dt>Avg max drawdown</dt><dd>${fmtSignedPct(o.avg_max_drawdown)}</dd></div>
          <div><dt>Episodes độc lập</dt><dd>${fmtNum(o.episodes)}</dd></div>
        </dl>
      </article>`;
  }).join('') : '<p class="br-empty">Chưa có kết quả theo kỳ nắm giữ.</p>';

  const notes = Array.isArray(d.notes) && d.notes.length
    ? `<ul class="br-notes">${d.notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>`
    : '';

  const analogs = (Array.isArray(d.analogs) ? d.analogs : [])
    .slice()
    .sort((a, b) => String(b.entry_date).localeCompare(String(a.entry_date)));
  const maxAnalogs = opts.maxAnalogs ?? (compact ? 8 : 60);
  const mainH = horizons.length ? horizons[horizons.length - 1] : null;
  const analogTable = analogs.length ? `
    <section class="br-section" aria-label="Các lần tương tự trước đây">
      <h3 class="br-h">Các lần tương tự <span class="br-h-sub">${fmtNum(analogs.length)} đợt, mới nhất trước</span></h3>
      <div class="tbl-scroll">
        <table class="br-table">
          <thead><tr>
            <th scope="col">Ngày vào</th>
            <th scope="col" class="num">Entry</th>
            ${horizons.map(h => `<th scope="col" class="num">Sau ${esc(h)}p</th>`).join('')}
            ${mainH != null ? `<th scope="col" class="num">Max drawdown (${esc(mainH)}p)</th>` : ''}
          </tr></thead>
          <tbody>
            ${analogs.slice(0, maxAnalogs).map(a => {
              const ret = a.returns ?? {};
              const dd = a.max_drawdown ?? {};
              return `<tr>
                <th scope="row">${fmtDateVi(a.entry_date)}</th>
                <td class="num">${fmtNum(a.entry_close, priceDecimals)}</td>
                ${horizons.map(h => {
                  const v = ret[h] ?? ret[String(h)];
                  return `<td class="num ${signClass(v)}">${fmtSignedPct(v)}</td>`;
                }).join('')}
                ${mainH != null ? `<td class="num">${fmtSignedPct(dd[mainH] ?? dd[String(mainH)])}</td>` : ''}
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>
      ${analogs.length > maxAnalogs ? `<p class="br-note">Hiển thị ${fmtNum(maxAnalogs)} / ${fmtNum(analogs.length)} đợt gần nhất.</p>` : ''}
    </section>` : '';

  return `
    <div class="br-root${compact ? ' br-root--compact' : ''}">
      ${head}
      ${state}
      <section class="br-section" aria-label="Kết quả theo kỳ nắm giữ">
        <div class="br-outcomes">${cards}</div>
        ${notes}
      </section>
      ${analogTable}
    </div>`;
}

// ── Drawer ───────────────────────────────────────────────────────────────────
const DRAWER_ID = 'brDrawer';
let _opener = null;
let _reqSeq = 0;
let _currentTicker = null;
let _currentOpts = {};

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function _ensureDrawer() {
  let root = document.getElementById(DRAWER_ID);
  if (root) return root;
  root = document.createElement('div');
  root.id = DRAWER_ID;
  root.className = 'br-drawer';
  root.hidden = true;
  root.innerHTML = `
    <div class="br-backdrop" data-br-close></div>
    <aside class="br-panel" role="dialog" aria-modal="true" aria-labelledby="brTitle" tabindex="-1">
      <header class="br-panel-head">
        <div>
          <p class="br-eyebrow" id="brEyebrow">Base rate lịch sử</p>
          <h2 class="br-title" id="brTitle">Base rate</h2>
        </div>
        <button type="button" class="br-close" data-br-close aria-label="Đóng">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </header>
      <div class="br-panel-body" id="brBody" aria-live="polite"></div>
      <footer class="br-panel-foot" id="brFoot"></footer>
    </aside>`;
  document.body.appendChild(root);

  root.addEventListener('click', (e) => {
    if (e.target.closest('[data-br-close]')) { closeBaserateDrawer(); return; }
    if (e.target.closest('[data-plan-new]')) { closeBaserateDrawer({ restoreFocus: false }); return; }
    if (e.target.closest('[data-br-retry]')) { _load(); return; }
    const trade = e.target.closest('[data-br-trade]');
    if (trade) {
      const type = trade.dataset.brTrade;
      const ticker = _currentTicker;
      closeBaserateDrawer({ restoreFocus: false });
      _openQuickTrade(ticker, type);
    }
  });

  root.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); closeBaserateDrawer(); return; }
    if (e.key !== 'Tab') return;
    const panel = root.querySelector('.br-panel');
    const items = [...panel.querySelectorAll(FOCUSABLE)].filter(n => n.offsetParent !== null);
    if (!items.length) { e.preventDefault(); panel.focus(); return; }
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === panel)) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus();
    }
  });
  return root;
}

async function _openQuickTrade(ticker, type) {
  try {
    const qt = await import('../portfolio/quick-trade.js?v=5');
    qt.init();
    qt.openModal(ticker, type === 'sell' ? 'sell' : 'buy', null, {});
  } catch (err) {
    console.warn('[board] quick-trade unavailable:', err);
  }
}

function _skeleton() {
  return `
    <div class="br-loading" aria-busy="true">
      <span class="br-skel" style="width:40%"></span>
      <span class="br-skel" style="width:90%"></span>
      <div class="br-outcomes">
        <span class="br-skel br-skel--card"></span>
        <span class="br-skel br-skel--card"></span>
      </div>
      <span class="sr-only">Đang tải base rate…</span>
    </div>`;
}

async function _load() {
  const root = _ensureDrawer();
  const body = root.querySelector('#brBody');
  const ticker = _currentTicker;
  const seq = ++_reqSeq;
  body.innerHTML = _skeleton();
  try {
    const url = _currentOpts.url ?? `${marketApiBase()}/baserate/${encodeURIComponent(ticker)}`;
    const data = await getJson(url);
    if (seq !== _reqSeq || root.hidden) return;
    body.innerHTML = renderBaserate(data, { priceDecimals: _currentOpts.priceDecimals ?? 0 });
  } catch (err) {
    if (seq !== _reqSeq || root.hidden) return;
    const notFound = String(err?.message ?? '').startsWith('404');
    body.innerHTML = `
      <div class="br-error" role="alert">
        <p>${notFound ? 'Không tìm thấy mã này trong dữ liệu thị trường.' : 'Không tải được base rate.'}</p>
        <p class="br-error-detail">${esc(err?.message ?? '')}</p>
        ${notFound ? '' : '<button type="button" class="ghost-btn" data-br-retry>Thử lại</button>'}
      </div>`;
  }
}

/**
 * @param {string} ticker
 * @param {{ inPortfolio?: boolean, tradable?: boolean, url?: string, title?: string, priceDecimals?: number }} [opts]
 */
export function openBaserateDrawer(ticker, opts = {}) {
  if (!ticker) return;
  const root = _ensureDrawer();
  _currentTicker = String(ticker).toUpperCase();
  _currentOpts = opts;
  _opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;

  root.querySelector('#brTitle').textContent = opts.title ?? `Base rate của ${_currentTicker}`;
  root.querySelector('#brEyebrow').textContent = opts.inPortfolio ? 'Đang nắm · base rate lịch sử' : 'Base rate lịch sử';
  const foot = root.querySelector('#brFoot');
  const tradable = opts.tradable !== false;
  foot.hidden = !tradable;
  foot.innerHTML = tradable ? `
    <button type="button" class="br-trade br-trade--buy" data-br-trade="buy">Mua ${esc(_currentTicker)}</button>
    ${opts.inPortfolio ? `<button type="button" class="br-trade br-trade--sell" data-br-trade="sell">Bán ${esc(_currentTicker)}</button>` : ''}
    <button type="button" class="br-trade br-trade--plan" data-plan-new="${esc(_currentTicker)}">Lập kế hoạch</button>` : '';

  root.hidden = false;
  document.body.classList.add('br-locked');
  root.querySelector('.br-panel').focus();
  _load();
}

export function closeBaserateDrawer({ restoreFocus = true } = {}) {
  const root = document.getElementById(DRAWER_ID);
  if (!root || root.hidden) return;
  root.hidden = true;
  _reqSeq++;
  document.body.classList.remove('br-locked');
  if (restoreFocus && _opener && document.contains(_opener)) _opener.focus();
  _opener = null;
}
