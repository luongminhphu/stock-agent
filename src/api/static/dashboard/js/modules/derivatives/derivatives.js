/**
 * derivatives.js — Tab "Phái sinh": HĐTL VN30, phòng hộ danh mục, basis, tiền lệ VN30,
 * máy tính lãi/lỗ vị thế.
 * Owner: modules/derivatives
 *
 * Nguồn (đều GET, prefix /api/v1/derivatives):
 *   /contracts            → { as_of, vn30, contracts[] }
 *   /hedge?ratio=&series= → kế hoạch Short HĐ theo beta (404 = chưa có vị thế mở)
 *   /vn30/baserate        → cùng shape với /market/baserate (kỳ 5/20/60 phiên)
 *   /basis?days=730       → thống kê basis lịch sử
 *
 * Exports:
 *   loadDerivatives() — render khung + fetch song song (gọi khi tab mở lần đầu)
 */

import { getJson } from '../../api/client.js?v=1';
import { esc } from '../../utils/format.js?v=1';
import {
  fmtNum, fmtVnd, fmtPct, signClass, fmtDateVi, renderBaserate,
} from '../board/baserate-drawer.js?v=2';

const BASE = '/api/v1/derivatives';
const SERIES = ['F1M', 'F2M', 'F1Q', 'F2Q'];
const SERIES_LABEL = Object.freeze({
  F1M: 'Tháng gần',
  F2M: 'Tháng kế',
  F1Q: 'Quý gần',
  F2Q: 'Quý xa',
});
const ROLL_DAYS = 5;

const _state = {
  contracts: null,
  ratio: 1.0,
  series: 'F1M',
  hedgeSeq: 0,
  bound: false,
};

const fmtPts = (v) => fmtNum(v, 1);
const fmtSignedPts = (v) => (v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${fmtPts(v)}`);

function _root() {
  return document.getElementById('derivativesRoot');
}

function _loading(text = 'Đang tải…') {
  return `<p class="dv-loading" aria-busy="true"><span class="dv-spinner" aria-hidden="true"></span>${esc(text)}</p>`;
}

function _error(msg, retryKey) {
  return `<div class="dv-state dv-state--error" role="alert">
    <p>Không tải được dữ liệu.</p>
    <p class="dv-state-detail">${esc(msg)}</p>
    ${retryKey ? `<button type="button" class="ghost-btn" data-dv-retry="${retryKey}">Thử lại</button>` : ''}
  </div>`;
}

function _is404(err) {
  return String(err?.message ?? '').startsWith('404');
}

// ── Skeleton ────────────────────────────────────────────────────────────────
function _shellHTML() {
  return `
    <section class="panel dv-panel" aria-labelledby="dvContractsTitle">
      <div class="panel-header split-header">
        <div>
          <p class="eyebrow">HĐ tương lai chỉ số VN30</p>
          <h2 id="dvContractsTitle">Kỳ hạn &amp; giá</h2>
        </div>
        <div class="dv-index" id="dvIndex" aria-live="polite"></div>
      </div>
      <div class="panel-body" id="dvContracts">${_loading()}</div>
    </section>

    <section class="panel dv-panel" aria-labelledby="dvHedgeTitle">
      <div class="panel-header">
        <p class="eyebrow">Hedge Portfolio cổ phiếu</p>
        <h2 id="dvHedgeTitle">Short HĐ VN30 theo beta</h2>
      </div>
      <div class="panel-body">
        <form class="dv-controls" id="dvHedgeForm">
          <label class="dv-field dv-field--slider">
            <span class="dv-label">Hedge ratio <output id="dvRatioOut" for="dvRatio">100%</output></span>
            <input type="range" id="dvRatio" min="0" max="100" step="10" value="100" aria-describedby="dvRatioHint" />
            <span class="dv-hint" id="dvRatioHint">0% = không hedge · 100% = trung hoà toàn bộ beta</span>
          </label>
          <label class="dv-field">
            <span class="dv-label">Kỳ hạn</span>
            <select id="dvSeries">
              ${SERIES.map(s => `<option value="${s}">${s} · ${SERIES_LABEL[s]}</option>`).join('')}
            </select>
          </label>
        </form>
        <div id="dvHedge" aria-live="polite">${_loading()}</div>
      </div>
    </section>

    <div class="dv-duo">
      <section class="panel dv-panel" aria-labelledby="dvVn30Title">
        <div class="panel-header">
          <p class="eyebrow">Thống kê, không dự báo</p>
          <h2 id="dvVn30Title">Base rate VN30</h2>
        </div>
        <div class="panel-body" id="dvVn30">${_loading()}</div>
      </section>

      <section class="panel dv-panel" aria-labelledby="dvBasisTitle">
        <div class="panel-header">
          <p class="eyebrow">2 năm gần nhất</p>
          <h2 id="dvBasisTitle">Basis lịch sử theo số ngày tới Expiry</h2>
        </div>
        <div class="panel-body" id="dvBasis">${_loading()}</div>
      </section>
    </div>`;
}

// ── Contracts ───────────────────────────────────────────────────────────────
function _renderContracts(d) {
  const idx = document.getElementById('dvIndex');
  const wrap = document.getElementById('dvContracts');
  if (!wrap) return;
  if (idx) {
    idx.innerHTML = `<span class="dv-index-label">VN30</span>
      <strong class="dv-index-val">${fmtPts(d?.vn30)}</strong>
      ${d?.as_of ? `<span class="dv-index-sub">ngày ${fmtDateVi(d.as_of)}</span>` : ''}`;
  }
  const rows = Array.isArray(d?.contracts) ? d.contracts : [];
  if (!rows.length) {
    wrap.innerHTML = '<p class="dv-state">Chưa có dữ liệu hợp đồng.</p>';
    return;
  }
  wrap.innerHTML = `
    <div class="tbl-scroll" tabindex="0" role="region" aria-label="Bảng hợp đồng tương lai">
      <table class="dv-table">
        <thead><tr>
          <th scope="col">Kỳ hạn</th>
          <th scope="col">Mã HĐ</th>
          <th scope="col">Expiry</th>
          <th scope="col" class="num">Còn</th>
          <th scope="col" class="num">Giá</th>
          <th scope="col" class="num" title="Giá HĐ − VN30. Dương: HĐ cao hơn chỉ số">Basis</th>
          <th scope="col" class="num">Giá trị 1 HĐ</th>
        </tr></thead>
        <tbody>
          ${rows.map(c => {
            const soon = c.days_to_expiry != null && c.days_to_expiry <= ROLL_DAYS;
            return `<tr class="${soon ? 'dv-row--soon' : ''}">
              <th scope="row">${esc(c.series)} <span class="dv-sub">${esc(SERIES_LABEL[c.series] ?? '')}</span></th>
              <td><span class="dv-code">${esc(c.code)}</span><span class="dv-sub">${esc(c.krx ?? '')}</span></td>
              <td>${fmtDateVi(c.expiry)}${soon ? '<span class="dv-warn-tag">Sắp đáo hạn – cân nhắc roll</span>' : ''}</td>
              <td class="num">${fmtNum(c.days_to_expiry)} ngày</td>
              <td class="num">${c.price == null ? '<span class="dv-muted">chưa có</span>' : fmtPts(c.price)}${c.price_source === 'continuous' ? '<span class="dv-sub" title="Bảng giá HĐ chưa có giá — dùng giá chuỗi liên tục VN30F1M (cập nhật ~5 phút)">theo VN30F1M</span>' : ''}</td>
              <td class="num">${fmtSignedPts(c.basis)}</td>
              <td class="num">${fmtVnd(c.contract_value)}</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>
    <p class="dv-note">1 điểm = 100.000 đ. Đáo hạn vào thứ Năm thứ ba của tháng.</p>`;
}

async function _loadContracts() {
  const wrap = document.getElementById('dvContracts');
  if (wrap) wrap.innerHTML = _loading();
  try {
    const d = await getJson(`${BASE}/contracts`);
    _state.contracts = d;
    _renderContracts(d);
  } catch (err) {
    if (wrap) wrap.innerHTML = _error(err?.message, 'contracts');
  }
}

// ── Sổ vị thế ───────────────────────────────────────────────────────────────
// ── Hedge ───────────────────────────────────────────────────────────────────
function _renderHedge(h) {
  const wrap = document.getElementById('dvHedge');
  if (!wrap) return;
  const warnings = [];
  if (h.price_is_index_proxy) warnings.push('Chưa có giá HĐ — tạm dùng mức VN30.');
  if (Array.isArray(h.missing_beta) && h.missing_beta.length) {
    warnings.push(`Thiếu lịch sử để tính beta cho ${h.missing_beta.map(esc).join(', ')} — giả định beta = 1.`);
  }
  const basisNote = h.basis_cost == null
    ? ''
    : h.basis_cost >= 0
      ? 'lợi khi hội tụ về đáo hạn'
      : 'chi phí khi hội tụ về đáo hạn';
  const holdings = Array.isArray(h.holdings) ? h.holdings : [];

  wrap.innerHTML = `
    <div class="dv-hedge-head">
      <p class="dv-hedge-main">Short <strong>${fmtNum(h.contracts_to_short)}</strong> hợp đồng <span class="dv-code">${esc(h.contract_code)}</span></p>
      <p class="dv-hedge-sub">giá HĐ ${fmtPts(h.futures_price)} điểm · phòng hộ ${fmtPct((h.hedge_ratio ?? 0) * 100, 0)} mức phơi nhiễm beta</p>
    </div>
    ${warnings.map(w => `<p class="dv-warn" role="note">${w}</p>`).join('')}
    <dl class="dv-kpis">
      <div><dt>Giá trị danh mục</dt><dd>${fmtVnd(h.portfolio_value)}</dd></div>
      <div><dt>Beta danh mục</dt><dd>${fmtNum(h.portfolio_beta, 2)}</dd></div>
      <div><dt>Phơi nhiễm theo beta</dt><dd>${fmtVnd(h.beta_exposure)}</dd></div>
      <div><dt>Giá trị được phòng hộ</dt><dd>${fmtVnd(h.hedged_value)}</dd></div>
      <div><dt>Ký quỹ cần</dt><dd>${fmtVnd(h.margin_required)}</dd></div>
      <div><dt>Basis</dt><dd>${fmtSignedPts(h.basis_points)} điểm${h.basis_cost != null ? `<span class="dv-sub ${signClass(h.basis_cost)}">${h.basis_cost > 0 ? '+' : ''}${fmtVnd(h.basis_cost)} đ · ${basisNote}</span>` : ''}</dd></div>
    </dl>
    ${holdings.length ? `
      <div class="tbl-scroll" tabindex="0" role="region" aria-label="Beta từng mã">
        <table class="dv-table dv-table--compact">
          <thead><tr>
            <th scope="col">Mã</th>
            <th scope="col" class="num">Giá trị</th>
            <th scope="col" class="num">Tỉ trọng</th>
            <th scope="col" class="num" title="Beta 120 phiên so với VN30">Beta</th>
          </tr></thead>
          <tbody>
            ${holdings.map(x => `<tr>
              <th scope="row">${esc(x.ticker)}</th>
              <td class="num">${fmtVnd(x.market_value)}</td>
              <td class="num">${fmtPct((x.weight ?? 0) * 100)}</td>
              <td class="num">${x.beta == null ? '<span class="dv-muted">giả định 1</span>' : fmtNum(x.beta, 2)}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>` : ''}`;
}

async function _loadHedge() {
  const wrap = document.getElementById('dvHedge');
  if (!wrap) return;
  const seq = ++_state.hedgeSeq;
  if (_state.ratio <= 0) {
    wrap.innerHTML = '<p class="dv-state">Tỉ lệ 0% — không phòng hộ, danh mục giữ nguyên rủi ro thị trường.</p>';
    return;
  }
  wrap.classList.add('is-refreshing');
  if (!wrap.firstElementChild || wrap.querySelector('.dv-state')) wrap.innerHTML = _loading('Đang tính kế hoạch phòng hộ…');
  try {
    const qs = new URLSearchParams({ ratio: String(_state.ratio), series: _state.series });
    const h = await getJson(`${BASE}/hedge?${qs}`);
    if (seq !== _state.hedgeSeq) return;
    _renderHedge(h ?? {});
  } catch (err) {
    if (seq !== _state.hedgeSeq) return;
    wrap.innerHTML = _is404(err)
      ? '<p class="dv-state">Chưa có vị thế cổ phiếu mở — kế hoạch phòng hộ sẽ hiện khi danh mục có mã đang nắm.</p>'
      : _error(err?.message, 'hedge');
  } finally {
    if (seq === _state.hedgeSeq) wrap.classList.remove('is-refreshing');
  }
}

// ── VN30 base rate + basis ──────────────────────────────────────────────────
async function _loadVn30() {
  const wrap = document.getElementById('dvVn30');
  if (!wrap) return;
  wrap.innerHTML = _loading();
  try {
    const d = await getJson(`${BASE}/vn30/baserate`);
    wrap.innerHTML = renderBaserate(d, { priceDecimals: 1, compact: true, maxAnalogs: 8 });
  } catch (err) {
    wrap.innerHTML = _error(err?.message, 'vn30');
  }
}

const DTE_BUCKETS = [
  ['0-3', '0–3 ngày'],
  ['4-10', '4–10 ngày'],
  ['11-20', '11–20 ngày'],
  ['>20', 'Trên 20 ngày'],
];

async function _loadBasis() {
  const wrap = document.getElementById('dvBasis');
  if (!wrap) return;
  wrap.innerHTML = _loading();
  try {
    const b = await getJson(`${BASE}/basis?days=730`);
    if (!b || !b.samples) {
      wrap.innerHTML = '<p class="dv-state">Chưa đủ dữ liệu basis lịch sử.</p>';
      return;
    }
    const by = b.by_days_to_expiry ?? {};
    wrap.innerHTML = `
      <div class="dv-basis-now">
        <div><span class="dv-label">Basis hiện tại</span><strong>${fmtSignedPts(b.current)}</strong></div>
        <div><span class="dv-label">Cao hơn</span><strong>${b.percentile_of_current == null ? '—' : fmtPct(b.percentile_of_current, 0)}</strong><span class="dv-sub">số phiên lịch sử</span></div>
        <div><span class="dv-label">Median · P10–P90</span><strong>${fmtSignedPts(b.median)}</strong><span class="dv-sub">${fmtSignedPts(b.p10)} … ${fmtSignedPts(b.p90)}</span></div>
      </div>
      <div class="tbl-scroll">
        <table class="dv-table dv-table--compact">
          <thead><tr>
            <th scope="col">Số ngày tới Expiry</th>
            <th scope="col" class="num">Median basis (điểm)</th>
          </tr></thead>
          <tbody>
            ${DTE_BUCKETS.map(([k, label]) => `<tr>
              <th scope="row">${label}</th>
              <td class="num">${by[k] == null ? '<span class="dv-muted">—</span>' : fmtSignedPts(by[k])}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>
      <p class="dv-note">${fmtNum(b.samples)} phiên mẫu. Basis = giá HĐ tháng gần − VN30; thường co về 0 khi gần đáo hạn.</p>`;
  } catch (err) {
    wrap.innerHTML = _error(err?.message, 'basis');
  }
}

// ── Events ──────────────────────────────────────────────────────────────────
function _debounce(fn, ms) {
  let t = null;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

function _bind(root) {
  if (_state.bound) return;
  _state.bound = true;
  const refetchHedge = _debounce(_loadHedge, 250);

  root.addEventListener('input', (e) => {
    if (e.target.id === 'dvRatio') {
      const v = Number(e.target.value);
      _state.ratio = v / 100;
      const outEl = document.getElementById('dvRatioOut');
      if (outEl) outEl.textContent = `${v}%`;
      refetchHedge();
    }
  });
  root.addEventListener('change', (e) => {
    if (e.target.id === 'dvSeries') {
      _state.series = SERIES.includes(e.target.value) ? e.target.value : 'F1M';
      _loadHedge();
    }
  });
  root.addEventListener('click', (e) => {
    const retry = e.target.closest('[data-dv-retry]');
    if (!retry) return;
    const key = retry.dataset.dvRetry;
    if (key === 'contracts') _loadContracts();
    else if (key === 'hedge') _loadHedge();
    else if (key === 'vn30') _loadVn30();
    else if (key === 'basis') _loadBasis();
  });
}

export async function loadDerivatives() {
  const root = _root();
  if (!root) return;
  if (!root.dataset.ready) {
    root.innerHTML = _shellHTML();
    root.dataset.ready = '1';
    _bind(root);
  }
  await Promise.allSettled([_loadContracts(), _loadHedge(), _loadVn30(), _loadBasis()]);
}
