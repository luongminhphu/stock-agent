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
 *   /position-preview     → lãi/lỗ, ký quỹ, cảnh báo đáo hạn
 *   /positions            → sổ vị thế (GET), mở (POST), đóng (POST /positions/{id}/close)
 *
 * Exports:
 *   loadDerivatives() — render khung + fetch song song (gọi khi tab mở lần đầu)
 */

import { getJson, sendJson } from '../../api/client.js?v=1';
import { showToast } from '../../utils/dom.js?v=1';
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
  previewSeq: 0,
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

    <section class="panel dv-panel" aria-labelledby="dvPosTitle">
      <div class="panel-header split-header">
        <div>
          <p class="eyebrow">Sổ vị thế · P&amp;L theo giá HĐ hiện tại</p>
          <h2 id="dvPosTitle">Vị thế của tôi</h2>
        </div>
        <button type="button" class="ghost-btn" data-dv-pos-new aria-expanded="false" aria-controls="dvPosForm">+ Ghi vị thế</button>
      </div>
      <div class="panel-body">
        <div id="dvPositions" aria-live="polite">${_loading()}</div>
        <form class="dv-calc dv-pos-form" id="dvPosForm" novalidate hidden>
          <label class="dv-field">
            <span class="dv-label">Mã HĐ</span>
            <select id="dvPosCode" required><option value="">Đang tải…</option></select>
          </label>
          <label class="dv-field">
            <span class="dv-label">Side</span>
            <select id="dvPosSide">
              <option value="LONG">Long (mua)</option>
              <option value="SHORT">Short (bán)</option>
            </select>
          </label>
          <label class="dv-field">
            <span class="dv-label">Số HĐ</span>
            <input id="dvPosQty" type="number" inputmode="numeric" min="1" max="10000" step="1" value="1" required />
          </label>
          <label class="dv-field">
            <span class="dv-label">Entry (điểm)</span>
            <input id="dvPosEntry" type="number" inputmode="decimal" min="0" step="0.1" required />
          </label>
          <label class="dv-field">
            <span class="dv-label">Mục đích</span>
            <select id="dvPosPurpose">
              <option value="hedge">Hedge Portfolio</option>
              <option value="trade">Trading</option>
            </select>
          </label>
          <div class="dv-field dv-field--submit">
            <button type="submit" class="primary-btn">Lưu vị thế</button>
          </div>
        </form>
      </div>
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
    </div>

    <section class="panel dv-panel" aria-labelledby="dvCalcTitle">
      <div class="panel-header">
        <p class="eyebrow">Máy tính</p>
        <h2 id="dvCalcTitle">P&amp;L &amp; Margin một vị thế</h2>
      </div>
      <div class="panel-body">
        <form class="dv-calc" id="dvCalcForm" novalidate>
          <label class="dv-field">
            <span class="dv-label">Mã HĐ</span>
            <select id="dvCalcCode" name="code" required><option value="">Đang tải…</option></select>
          </label>
          <label class="dv-field">
            <span class="dv-label">Side</span>
            <select id="dvCalcSide" name="side">
              <option value="LONG">Long (mua)</option>
              <option value="SHORT">Short (bán)</option>
            </select>
          </label>
          <label class="dv-field">
            <span class="dv-label">Số HĐ</span>
            <input id="dvCalcQty" name="qty" type="number" inputmode="numeric" min="1" max="10000" step="1" value="1" required />
          </label>
          <label class="dv-field">
            <span class="dv-label">Entry (điểm)</span>
            <input id="dvCalcEntry" name="entry" type="number" inputmode="decimal" min="0" step="0.1" required />
          </label>
          <label class="dv-field">
            <span class="dv-label">Mark (điểm)</span>
            <input id="dvCalcMark" name="mark" type="number" inputmode="decimal" min="0" step="0.1" required />
          </label>
          <div class="dv-field dv-field--submit">
            <button type="submit" class="primary-btn">Tính</button>
          </div>
        </form>
        <div id="dvCalcResult" aria-live="polite"></div>
      </div>
    </section>`;
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
              <td class="num">${c.price == null ? '<span class="dv-muted">chưa có</span>' : fmtPts(c.price)}</td>
              <td class="num">${fmtSignedPts(c.basis)}</td>
              <td class="num">${fmtVnd(c.contract_value)}</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>
    <p class="dv-note">1 điểm = 100.000 đ. Đáo hạn vào thứ Năm thứ ba của tháng.</p>`;
}

function _fillCalcDefaults(d) {
  const sel = document.getElementById('dvCalcCode');
  if (!sel) return;
  const rows = Array.isArray(d?.contracts) ? d.contracts : [];
  if (!rows.length) {
    sel.innerHTML = '<option value="">Không có HĐ</option>';
    return;
  }
  sel.innerHTML = rows.map(c =>
    `<option value="${esc(c.code)}" data-price="${c.price ?? ''}">${esc(c.code)} · ${esc(c.series)}</option>`,
  ).join('');
  const f1 = rows.find(c => c.series === 'F1M') ?? rows[0];
  sel.value = f1.code;
  const px = f1.price ?? d.vn30;
  const entry = document.getElementById('dvCalcEntry');
  const mark = document.getElementById('dvCalcMark');
  if (px != null) {
    if (entry && !entry.value) entry.value = Number(px).toFixed(1);
    if (mark && !mark.value) mark.value = Number(px).toFixed(1);
  }
}

async function _loadContracts() {
  const wrap = document.getElementById('dvContracts');
  if (wrap) wrap.innerHTML = _loading();
  try {
    const d = await getJson(`${BASE}/contracts`);
    _state.contracts = d;
    _renderContracts(d);
    _fillCalcDefaults(d);
    _fillPosCodes(d);
  } catch (err) {
    if (wrap) wrap.innerHTML = _error(err?.message, 'contracts');
    const sel = document.getElementById('dvCalcCode');
    if (sel) sel.innerHTML = '<option value="">Không tải được HĐ</option>';
  }
}

// ── Sổ vị thế ───────────────────────────────────────────────────────────────
function _fillPosCodes(d) {
  const sel = document.getElementById('dvPosCode');
  if (!sel) return;
  const rows = Array.isArray(d?.contracts) ? d.contracts : [];
  sel.innerHTML = rows.length
    ? rows.map(c => `<option value="${esc(c.code)}" data-price="${c.price ?? ''}">${esc(c.code)} · ${esc(SERIES_LABEL[c.series] ?? c.series)}</option>`).join('')
    : '<option value="">Không có HĐ</option>';
  const first = rows.find(c => c.series === 'F1M') ?? rows[0];
  const entry = document.getElementById('dvPosEntry');
  if (first && entry && !entry.value && (first.price ?? d.vn30) != null) entry.value = Number(first.price ?? d.vn30).toFixed(1);
}

function _posRowHTML(p) {
  const side = String(p.side).toUpperCase() === 'SHORT'
    ? '<span class="dv-side dv-side--short">Short</span>'
    : '<span class="dv-side dv-side--long">Long</span>';
  const pnl = p.unrealized_pnl == null
    ? '<span class="dv-muted">chưa có giá</span>'
    : `<span class="${signClass(p.unrealized_pnl)}">${p.unrealized_pnl > 0 ? '+' : ''}${fmtVnd(p.unrealized_pnl)}</span>
       <span class="dv-sub ${signClass(p.pnl_points)}">${fmtSignedPts(p.pnl_points)} điểm · ${p.pnl_on_margin_pct > 0 ? '+' : ''}${fmtPct(p.pnl_on_margin_pct)} trên Margin</span>`;
  const expiry = p.expiry
    ? `${fmtDateVi(p.expiry)}<span class="dv-sub">còn ${fmtNum(p.days_to_expiry)} ngày</span>${p.roll_warning ? '<span class="dv-warn-tag">Nên Roll</span>' : ''}`
    : '<span class="dv-muted">—</span>';
  return `<tr data-pos-id="${Number(p.id)}">
    <th scope="row"><span class="dv-code">${esc(p.contract_code)}</span>
      <span class="dv-sub">${p.purpose === 'hedge' ? 'Hedge' : p.purpose === 'trade' ? 'Trading' : ''}${p.note ? ` · ${esc(p.note)}` : ''}</span></th>
    <td>${side}</td>
    <td class="num">${fmtNum(p.qty)}</td>
    <td class="num">${fmtPts(p.entry_price)}</td>
    <td class="num">${p.mark_price == null ? '<span class="dv-muted">—</span>' : fmtPts(p.mark_price)}</td>
    <td class="num">${pnl}</td>
    <td class="num">${p.initial_margin == null ? '—' : fmtVnd(p.initial_margin)}</td>
    <td>${expiry}</td>
    <td class="num"><button type="button" class="ghost-btn dv-pos-close" data-dv-pos-close="${Number(p.id)}" data-mark="${p.mark_price ?? ''}">Đóng</button></td>
  </tr>`;
}

async function _loadPositions() {
  const wrap = document.getElementById('dvPositions');
  if (!wrap) return;
  try {
    const d = await getJson(`${BASE}/positions`);
    const rows = (Array.isArray(d?.positions) ? d.positions : []).filter(p => !p.closed_at);
    if (!rows.length) {
      wrap.innerHTML = `<p class="dv-state">Chưa có vị thế phái sinh nào đang mở.${
        d?.realized_pnl ? ` Realized P&amp;L: <strong class="${signClass(d.realized_pnl)}">${fmtVnd(d.realized_pnl)} đ</strong>.` : ''}</p>`;
      return;
    }
    wrap.innerHTML = `
      <dl class="dv-kpis">
        <div><dt>Unrealized P&amp;L</dt><dd class="${signClass(d.open_unrealized_pnl)}">${d.open_unrealized_pnl > 0 ? '+' : ''}${fmtVnd(d.open_unrealized_pnl)} đ</dd></div>
        <div><dt>Margin đang dùng</dt><dd>${fmtVnd(d.open_margin)} đ</dd></div>
        <div><dt>Realized P&amp;L</dt><dd class="${signClass(d.realized_pnl)}">${d.realized_pnl > 0 ? '+' : ''}${fmtVnd(d.realized_pnl)} đ</dd></div>
      </dl>
      <div class="tbl-scroll" tabindex="0" role="region" aria-label="Vị thế phái sinh đang mở">
        <table class="dv-table">
          <thead><tr>
            <th scope="col">Mã HĐ</th><th scope="col">Side</th><th scope="col" class="num">Số HĐ</th>
            <th scope="col" class="num">Entry</th><th scope="col" class="num">Mark</th>
            <th scope="col" class="num">P&amp;L (đ)</th><th scope="col" class="num">Margin</th>
            <th scope="col">Expiry</th><th scope="col"><span class="sr-only">Thao tác</span></th>
          </tr></thead>
          <tbody>${rows.map(_posRowHTML).join('')}</tbody>
        </table>
      </div>`;
  } catch (err) {
    wrap.innerHTML = _error(err?.message, 'positions');
  }
}

async function _submitPosition() {
  const code = document.getElementById('dvPosCode')?.value;
  const qty = parseInt(document.getElementById('dvPosQty')?.value ?? '', 10);
  const entry = parseFloat(document.getElementById('dvPosEntry')?.value ?? '');
  if (!code || !(qty >= 1) || !(entry > 0)) {
    showToast('Nhập đủ mã HĐ, số HĐ (≥ 1) và giá khớp.', 'error');
    return;
  }
  try {
    await sendJson(`${BASE}/positions`, 'POST', {
      contract_code: code,
      side: document.getElementById('dvPosSide')?.value ?? 'LONG',
      qty,
      entry_price: entry,
      purpose: document.getElementById('dvPosPurpose')?.value ?? null,
    });
    showToast(`Đã ghi vị thế ${code}`);
    const form = document.getElementById('dvPosForm');
    if (form) form.hidden = true;
    document.querySelector('[data-dv-pos-new]')?.setAttribute('aria-expanded', 'false');
    _loadPositions();
  } catch (err) {
    showToast(`Chưa ghi được: ${err.message}`, 'error');
  }
}

async function _closePosition(btn) {
  const id = Number(btn.dataset.dvPosClose);
  const mark = btn.dataset.mark;
  const input = window.prompt('Giá đóng vị thế (điểm):', mark ? Number(mark).toFixed(1) : '');
  if (input == null) return;
  const exit = parseFloat(String(input).replace(',', '.'));
  if (!(exit > 0)) { showToast('Giá đóng không hợp lệ.', 'error'); return; }
  btn.disabled = true;
  try {
    const r = await sendJson(`${BASE}/positions/${id}/close`, 'POST', { exit_price: exit });
    showToast(`Đã đóng vị thế · Realized P&L ${r?.realized_pnl > 0 ? '+' : ''}${fmtVnd(r?.realized_pnl)} đ`);
    _loadPositions();
  } catch (err) {
    btn.disabled = false;
    showToast(`Chưa đóng được: ${err.message}`, 'error');
  }
}

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

// ── Calculator ──────────────────────────────────────────────────────────────
async function _submitCalc() {
  const out = document.getElementById('dvCalcResult');
  if (!out) return;
  const code = document.getElementById('dvCalcCode')?.value ?? '';
  const side = document.getElementById('dvCalcSide')?.value ?? 'LONG';
  const qty = parseInt(document.getElementById('dvCalcQty')?.value ?? '', 10);
  const entry = parseFloat(document.getElementById('dvCalcEntry')?.value ?? '');
  const mark = parseFloat(document.getElementById('dvCalcMark')?.value ?? '');
  if (!code || !(qty >= 1) || !(entry > 0) || !(mark > 0)) {
    out.innerHTML = '<p class="dv-warn" role="alert">Nhập đủ mã HĐ, số HĐ (≥ 1), giá vào và giá hiện tại (> 0).</p>';
    return;
  }
  const seq = ++_state.previewSeq;
  out.innerHTML = _loading('Đang tính…');
  try {
    const qs = new URLSearchParams({ code, side, qty: String(qty), entry: String(entry), mark: String(mark) });
    const p = await getJson(`${BASE}/position-preview?${qs}`);
    if (seq !== _state.previewSeq) return;
    out.innerHTML = `
      ${p.roll_warning ? `<p class="dv-warn" role="note">HĐ còn ${fmtNum(p.days_to_expiry)} ngày tới đáo hạn — cân nhắc roll sang kỳ sau.</p>` : ''}
      <dl class="dv-kpis">
        <div><dt>P&amp;L</dt><dd class="${signClass(p.unrealized_pnl)}">${p.unrealized_pnl > 0 ? '+' : ''}${fmtVnd(p.unrealized_pnl)} đ</dd></div>
        <div><dt>Điểm</dt><dd class="${signClass(p.pnl_points)}">${fmtSignedPts(p.pnl_points)}</dd></div>
        <div><dt>P&amp;L trên Margin</dt><dd class="${signClass(p.pnl_on_margin_pct)}">${p.pnl_on_margin_pct > 0 ? '+' : ''}${fmtPct(p.pnl_on_margin_pct)}</dd></div>
        <div><dt>Notional</dt><dd>${fmtVnd(p.notional)}</dd></div>
        <div><dt>Initial margin</dt><dd>${fmtVnd(p.initial_margin)}</dd></div>
        <div><dt>Expiry</dt><dd>${fmtDateVi(p.expiry)}${p.days_to_expiry != null ? `<span class="dv-sub">còn ${fmtNum(p.days_to_expiry)} ngày</span>` : ''}</dd></div>
      </dl>`;
  } catch (err) {
    if (seq !== _state.previewSeq) return;
    out.innerHTML = _error(err?.message, null);
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
    } else if (e.target.id === 'dvCalcCode') {
      const opt = e.target.selectedOptions?.[0];
      const px = opt?.dataset.price;
      const mark = document.getElementById('dvCalcMark');
      if (px && mark) mark.value = Number(px).toFixed(1);
    } else if (e.target.id === 'dvPosCode') {
      const px = e.target.selectedOptions?.[0]?.dataset.price;
      const entry = document.getElementById('dvPosEntry');
      if (px && entry) entry.value = Number(px).toFixed(1);
    }
  });
  root.addEventListener('submit', (e) => {
    e.preventDefault();
    if (e.target.id === 'dvCalcForm') _submitCalc();
    else if (e.target.id === 'dvPosForm') _submitPosition();
  });
  root.addEventListener('click', (e) => {
    const nw = e.target.closest('[data-dv-pos-new]');
    if (nw) {
      const form = document.getElementById('dvPosForm');
      if (form) {
        form.hidden = !form.hidden;
        nw.setAttribute('aria-expanded', String(!form.hidden));
        if (!form.hidden) document.getElementById('dvPosQty')?.focus();
      }
      return;
    }
    const close = e.target.closest('[data-dv-pos-close]');
    if (close) { _closePosition(close); return; }
    const retry = e.target.closest('[data-dv-retry]');
    if (!retry) return;
    const key = retry.dataset.dvRetry;
    if (key === 'contracts') _loadContracts();
    else if (key === 'hedge') _loadHedge();
    else if (key === 'vn30') _loadVn30();
    else if (key === 'basis') _loadBasis();
    else if (key === 'positions') _loadPositions();
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
  await Promise.allSettled([_loadContracts(), _loadPositions(), _loadHedge(), _loadVn30(), _loadBasis()]);
}
