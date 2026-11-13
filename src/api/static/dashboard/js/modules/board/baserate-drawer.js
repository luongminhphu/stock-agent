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
  NGHIENG_MUA:      { label: 'Bullish',           cls: 'sg-buy' },
  NGHIENG_TRANH:    { label: 'Bearish',           cls: 'sg-avoid' },
  TRUNG_TINH:       { label: 'Neutral',           cls: 'sg-neutral' },
  KHONG_DU_TIEN_LE: { label: 'Insufficient data', cls: 'sg-insufficient' },
});

// Thuật ngữ quốc tế giữ tiếng Anh. Mã nội bộ (NGHIENG_MUA…) không đổi.
export const CONFIDENCE = Object.freeze({
  khong_du:   'N/A',
  thap:       'Low',
  trung_binh: 'Medium',
  cao:        'High',
});

const CONFIDENCE_LONG = Object.freeze({
  khong_du:   'N/A — chưa đủ episode',
  thap:       'Low',
  trung_binh: 'Medium',
  cao:        'High',
});

/** Tên đặc trưng trạng thái (khóa của matched_on / dropped). */
const FEATURE_NAMES = Object.freeze({
  trend:       'xu hướng MA50/MA200',
  market:      'thị trường chung (VN-Index)',
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
const TONE = Object.freeze({
  act:     { cls: 'br-next--act',     label: 'Có thể hành động' },
  watch:   { cls: 'br-next--watch',   label: 'Theo dõi' },
  caution: { cls: 'br-next--caution', label: 'Thận trọng' },
});

function _nextStepHTML(n, ticker) {
  if (!n || typeof n !== 'object') return '';
  const t = TONE[n.tone] ?? TONE.watch;
  const steps = Array.isArray(n.steps) ? n.steps : [];
  return `
    <section class="br-next ${t.cls}" aria-label="Nên làm gì tiếp theo">
      <p class="br-next-eyebrow">Nên làm gì tiếp theo · <span>${t.label}</span></p>
      <p class="br-next-head">${esc(n.headline)}</p>
      ${steps.length ? `<ul class="br-next-steps">${steps.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
      ${n.plan_cta && ticker ? `<button type="button" class="primary-btn br-next-cta" data-plan-new="${esc(ticker)}">Lập Kế hoạch nắm giữ ${esc(ticker)}</button>` : ''}
      <p class="br-next-foot">Diễn giải theo quy tắc từ thống kê tiền lệ bên dưới — không phải khuyến nghị đầu tư.</p>
    </section>`;
}

function _pathHTML(p) {
  if (!p || typeof p !== 'object' || !p.episodes) return '';
  const seg = (v, cls, label) => (_isNum(v) && v > 0
    ? `<span class="br-path-seg ${cls}" style="flex:${Number(v)}" title="${label} ${fmtProb(v, 0)}"></span>` : '');
  const lv = [
    _isNum(p.stop_pct) ? `Stop-loss −${fmtPct(p.stop_pct * 100, 0)}` : null,
    _isNum(p.target_pct) ? `Target +${fmtPct(p.target_pct * 100, 0)}` : null,
  ].filter(Boolean).join(' · ');
  const days = (v) => (_isNum(v) ? ` · trung vị ${fmtNum(v)} phiên` : '');
  return `
    <section class="br-section" aria-label="Xác suất chạm Stop-loss / Target">
      <h3 class="br-h">Chạm Target hay Stop-loss trước? <span class="br-h-sub">${esc(lv)} · trong ${esc(p.horizon)} phiên · ${fmtNum(p.episodes)} lần tiền lệ</span></h3>
      <div class="br-path-bar" role="img" aria-label="Chạm Target trước ${fmtProb(p.target_first, 0)}, chạm Stop-loss trước ${fmtProb(p.stop_first, 0)}, chưa chạm ${fmtProb(p.neither, 0)}">
        ${seg(p.target_first, 'is-target', 'Chạm Target trước')}${seg(p.neither, 'is-neither', 'Hết kỳ chưa chạm')}${seg(p.stop_first, 'is-stop', 'Chạm Stop-loss trước')}
      </div>
      <dl class="br-path-legend">
        <div><dt><i class="br-key is-target"></i>Chạm Target trước</dt><dd>${fmtProb(p.target_first, 0)}${days(p.median_days_to_target)}</dd></div>
        <div><dt><i class="br-key is-neither"></i>Hết kỳ chưa chạm</dt><dd>${fmtProb(p.neither, 0)}</dd></div>
        <div><dt><i class="br-key is-stop"></i>Chạm Stop-loss trước</dt><dd>${fmtProb(p.stop_first, 0)}${days(p.median_days_to_stop)}</dd></div>
      </dl>
      <p class="br-note">Mức mặc định như khi Lập kế hoạch: Stop-loss sâu hơn mức sụt điển hình 20%, Target = trung vị lợi nhuận các lần trước. Cùng phiên chạm cả hai tính là chạm Stop-loss.</p>
    </section>`;
}

const VERDICT = Object.freeze({
  dang_tin: { label: 'Đáng tin', cls: 'is-good', text: 'Trong quá khứ, khi app nói Bullish/Bearish với mã này, kết quả tốt hơn mức nền rõ rệt.' },
  yeu:      { label: 'Yếu',      cls: 'is-bad',  text: 'Trong quá khứ, tín hiệu của mã này không tốt hơn mức nền — đừng dựa nhiều vào nó.' },
  chua_du:  { label: 'Chưa đủ',  cls: 'is-muted', text: 'Quá ít lần có tín hiệu trong quá khứ để kết luận độ tin.' },
});

/** Kiểm chứng walk-forward: tín hiệu trong quá khứ đúng tới đâu. */
export function renderTrackRecord(r) {
  if (!r || typeof r !== 'object') return '';
  const v = VERDICT[r.verdict] ?? VERDICT.chua_du;
  const sig = Object.fromEntries((Array.isArray(r.signals) ? r.signals : []).map(x => [x.suggestion, x]));
  const row = (code, hitLabel) => {
    const x = sig[code];
    if (!x) return '';
    return `<tr>
      <th scope="row">${suggestionBadge(code)}</th>
      <td class="num">${fmtNum(x.count)}</td>
      <td class="num">${hitLabel && _isNum(x.hit_rate) ? `${fmtProb(x.hit_rate, 0)} <span class="br-sub">${hitLabel}</span>` : '—'}</td>
      <td class="num ${signClass(x.avg_return)}">${fmtSignedPct(x.avg_return)}</td>
    </tr>`;
  };
  const cal = Array.isArray(r.calibration) ? r.calibration : [];
  return `
    <h3 class="br-h">Độ tin của tín hiệu <span class="br-h-sub">kiểm chứng ${esc(r.horizon)} phiên · ${r.period_from ? fmtDateVi(r.period_from) : ''} – ${r.period_to ? fmtDateVi(r.period_to) : ''}</span></h3>
    <p class="br-verdict"><span class="br-verdict-tag ${v.cls}">${v.label}</span> ${esc(v.text)}</p>
    <div class="tbl-scroll" tabindex="0" role="region" aria-label="Tín hiệu trong quá khứ">
      <table class="br-table">
        <thead><tr><th scope="col">Tín hiệu khi đó</th><th scope="col" class="num">Số lần</th><th scope="col" class="num">Đúng</th><th scope="col" class="num">Lợi nhuận TB sau ${esc(r.horizon)}p</th></tr></thead>
        <tbody>
          ${row('NGHIENG_MUA', 'lãi')}
          ${row('NGHIENG_TRANH', 'giảm')}
          ${row('TRUNG_TINH', null)}
          <tr class="br-row-base"><th scope="row">Mọi lần kiểm (mức nền)</th><td class="num">${fmtNum(r.checked)}</td><td class="num">${fmtProb(r.base_win_rate, 0)} <span class="br-sub">lãi</span></td><td class="num ${signClass(r.base_avg_return)}">${fmtSignedPct(r.base_avg_return)}</td></tr>
        </tbody>
      </table>
    </div>
    ${cal.length ? `
      <details class="br-cal">
        <summary>Win rate app đưa ra có khớp thực tế không? (calibration)</summary>
        <table class="br-table">
          <thead><tr><th scope="col">App nói</th><th scope="col" class="num">Số lần</th><th scope="col" class="num">TB app nói</th><th scope="col" class="num">Thực tế lãi</th></tr></thead>
          <tbody>${cal.map(b => `<tr><th scope="row">${esc(b.label)}</th><td class="num">${fmtNum(b.count)}</td><td class="num">${fmtProb(b.predicted, 0)}</td><td class="num">${fmtProb(b.realized, 0)}</td></tr>`).join('')}</tbody>
        </table>
      </details>` : ''}
    ${(Array.isArray(r.notes) ? r.notes : []).map(n => `<p class="br-note">${esc(n)}</p>`).join('')}`;
}

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

  const co = d.company && typeof d.company === 'object' ? d.company : null;
  const closeLine = _isNum(d.last_close)
    ? `<span class="br-company-close">Đóng cửa ${d.as_of ? fmtDateVi(d.as_of) : 'gần nhất'}: <strong>${fmtNum(d.last_close, priceDecimals)}</strong></span>`
    : '';
  const company = co ? `
    <section class="br-company" aria-label="Thông tin công ty">
      <p class="br-company-name">${esc(co.name)}</p>
      <p class="br-company-meta">
        <span class="br-company-ticker">${esc(d.ticker)}</span>
        <span>Sàn ${esc(co.exchange)}</span>
        ${co.sector ? `<span>${esc(co.sector)}</span>` : ''}
        ${closeLine}
      </p>
      ${co.name_verified ? '' : '<p class="br-company-warn">Chưa có tên công ty từ danh sách niêm yết — hãy đối chiếu mã và giá trên app công ty chứng khoán.</p>'}
    </section>` : (closeLine ? `<p class="br-company-meta">${closeLine}</p>` : '');
  const head = `
    ${company}
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
      ${opts.nextStep === false ? '' : _nextStepHTML(d.next_step, opts.planTicker ?? d.ticker)}
      ${state}
      <section class="br-section" aria-label="Kết quả theo kỳ nắm giữ">
        <div class="br-outcomes">${cards}</div>
        ${notes}
      </section>
      ${_pathHTML(d.path)}
      ${opts.trackRecord ? '<section class="br-section br-track" id="brTrack" aria-label="Độ tin của tín hiệu" aria-live="polite"><p class="br-note">Đang kiểm chứng tín hiệu trên dữ liệu quá khứ…</p></section>' : ''}
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
    const custom = !!_currentOpts.url;
    const base = `${marketApiBase()}/baserate/${encodeURIComponent(ticker)}`;
    const url = custom ? _currentOpts.url : `${base}?holding=${_currentOpts.inPortfolio ? 'true' : 'false'}`;
    const data = await getJson(url);
    if (seq !== _reqSeq || root.hidden) return;
    body.innerHTML = renderBaserate(data, {
      priceDecimals: _currentOpts.priceDecimals ?? 0,
      trackRecord: !custom,
      planTicker: _currentOpts.tradable === false ? null : ticker,
    });
    if (!custom) _loadTrack(`${base}/track-record`, seq);
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

async function _loadTrack(url, seq) {
  const root = document.getElementById(DRAWER_ID);
  try {
    const r = await getJson(url);
    if (seq !== _reqSeq || !root || root.hidden) return;
    const box = root.querySelector('#brTrack');
    if (box) box.innerHTML = renderTrackRecord(r);
    // Tín hiệu của mã này từng không đáng tin → nhắc ngay trong khung "Nên làm gì tiếp theo"
    const next = root.querySelector('.br-next-steps, .br-next-head');
    if (next && r?.verdict === 'yeu') {
      next.insertAdjacentHTML('afterend', '<p class="br-next-warn" role="note">Lưu ý: kiểm chứng lịch sử cho thấy tín hiệu của mã này từng không tốt hơn mức nền — giảm khối lượng hoặc chỉ theo dõi.</p>');
    }
  } catch (err) {
    if (seq !== _reqSeq || !root || root.hidden) return;
    const box = root.querySelector('#brTrack');
    if (box) box.innerHTML = `<p class="br-note">Chưa kiểm chứng được độ tin: ${esc(err?.message ?? '')}</p>`;
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
