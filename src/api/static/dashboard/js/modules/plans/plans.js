/**
 * plans.js — Tab "Kế hoạch": mỗi mã nắm giữ có một kế hoạch dễ hiểu.
 * Owner: modules/plans
 *
 * Nguồn (prefix /api/v1/plans):
 *   GET  /plans              → kế hoạch đang mở: đèn, lý do, tóm tắt, điều tôi tin, sự kiện
 *   POST /plans/check        → kiểm tra lại ngay (bình thường chạy tự động 15:30)
 *   POST /plans/{id}/explain → AI diễn giải các con số đã tính (chỉ khi người dùng bấm)
 *
 * Đèn: xanh = đúng kế hoạch · vang = cần để ý · do = xem lại ngay · xam = chưa đủ dữ liệu.
 *
 * Exports:
 *   loadPlans({ silent })  — fetch + render vào #plansRoot
 *   getPlanLights()        — Map ticker → { light, id } của lần tải gần nhất (cho bảng danh mục)
 *   LIGHTS                 — nhãn/mô tả từng màu đèn
 */

import { getJson, sendJson } from '../../api/client.js?v=1';
import { esc } from '../../utils/format.js?v=1';
import { showToast } from '../../utils/dom.js?v=1';
import { fmtVnd, fmtPct, signClass, fmtDateVi, openBaserateDrawer } from '../board/baserate-drawer.js?v=2';
import { openPlanForm } from './plan-form.js?v=1';

const BASE = '/api/v1/plans';

export const LIGHTS = Object.freeze({
  do:   { label: 'Xem lại ngay',      hint: 'Có điều kiện trong kế hoạch đã bị phá vỡ.' },
  vang: { label: 'Cần để ý',          hint: 'Có dấu hiệu nên xem lại, chưa phá kế hoạch.' },
  xanh: { label: 'Đúng kế hoạch',     hint: 'Mọi điều bạn đặt ra vẫn đang đúng.' },
  xam:  { label: 'Chưa đủ dữ liệu',   hint: 'Chưa có giá hoặc kế hoạch đã đóng.' },
});

const BELIEF = Object.freeze({
  dung:    { icon: '✓', label: 'vẫn đúng' },
  sai:     { icon: '✕', label: 'không còn đúng' },
  chua_ro: { icon: '?', label: 'chưa đủ số liệu' },
  tu_kiem: { icon: '•', label: 'bạn tự theo dõi' },
});

let _plans = [];
let _seq = 0;
let _bound = false;
const _lights = new Map();

const _root = () => document.getElementById('plansRoot');
const _light = (l) => (LIGHTS[l] ? l : 'xam');

export function getPlanLights() {
  return _lights;
}

// ── Pieces ──────────────────────────────────────────────────────────────────
function _signedPct(v) {
  if (v == null) return '—';
  return `${v > 0 ? '+' : ''}${fmtPct(v)}`;
}

/** Thanh vị trí giá giữa "mức chấp nhận sai" và "mục tiêu". */
function _trackHTML(p) {
  const stop = Number(p.stop_price);
  const target = Number(p.target_price);
  const price = Number(p.price);
  const entry = Number(p.entry_price);
  if (!(stop > 0) || !(target > stop) || !(price > 0)) return '';
  const pos = (v) => Math.max(0, Math.min(100, ((v - stop) / (target - stop)) * 100));
  const pricePos = pos(price);
  const entryMark = entry > 0
    ? `<span class="pl-track-entry" style="left:${pos(entry).toFixed(1)}%" title="Entry ${fmtVnd(entry)}"></span>`
    : '';
  const outside = price <= stop ? ' pl-track--below' : price >= target ? ' pl-track--above' : '';
  return `
    <div class="pl-track${outside}" role="img"
      aria-label="Giá ${fmtVnd(price)} nằm giữa Stop-loss ${fmtVnd(stop)} và Target ${fmtVnd(target)}">
      <div class="pl-track-bar">
        <span class="pl-track-fill" style="width:${pricePos.toFixed(1)}%"></span>
        ${entryMark}
        <span class="pl-track-now" style="left:${pricePos.toFixed(1)}%"></span>
      </div>
      <div class="pl-track-legend">
        <span><span class="pl-track-k">Stop-loss</span>${fmtVnd(stop)}</span>
        ${entry > 0 ? `<span class="pl-track-mid"><span class="pl-track-k">Entry</span>${fmtVnd(entry)}</span>` : ''}
        <span class="pl-track-end"><span class="pl-track-k">Target</span>${fmtVnd(target)}</span>
      </div>
    </div>`;
}

function _factsHTML(p) {
  const items = [];
  if (p.to_stop_pct != null) {
    items.push(`<div><dt>Cách Stop-loss</dt><dd class="${p.to_stop_pct <= 0 ? 'is-down' : ''}">${
      p.to_stop_pct <= 0 ? 'đã thủng' : fmtPct(p.to_stop_pct)}</dd></div>`);
  }
  if (p.to_target_pct != null) {
    items.push(`<div><dt>Tới Target</dt><dd>${p.to_target_pct <= 0 ? 'đã tới' : fmtPct(p.to_target_pct)}</dd></div>`);
  }
  if (p.review_by) {
    const left = p.days_left;
    const sub = left == null ? '' : left <= 0 ? ' · đã tới hạn' : ` · còn ${left} ngày`;
    items.push(`<div><dt>Xem lại</dt><dd>${fmtDateVi(p.review_by)}<span class="pl-sub">${sub}</span></dd></div>`);
  }
  return items.length ? `<dl class="pl-facts">${items.join('')}</dl>` : '';
}

function _beliefsHTML(beliefs) {
  if (!Array.isArray(beliefs) || !beliefs.length) {
    return '<p class="pl-muted">Chưa ghi điều bạn tin khi mua.</p>';
  }
  return `<ul class="pl-beliefs">${beliefs.map(b => {
    const k = BELIEF[b.result] ? b.result : 'chua_ro';
    return `<li class="pl-belief pl-belief--${k}">
      <span class="pl-belief-icon" aria-hidden="true">${BELIEF[k].icon}</span>
      <span class="pl-belief-text">${esc(b.text || '')}</span>
      <span class="pl-belief-state">${BELIEF[k].label}</span>
    </li>`;
  }).join('')}</ul>`;
}

function _eventsHTML(events) {
  const open = (Array.isArray(events) ? events : []).filter(e => e.status !== 'cancelled');
  if (!open.length) return '';
  return `<p class="pl-events"><span class="pl-events-k">Đang chờ:</span> ${open.map(e =>
    `${esc(e.text)}${e.expected_date ? ` <span class="pl-sub">(${fmtDateVi(e.expected_date)})</span>` : ''}`,
  ).join(' · ')}</p>`;
}

function _cardHTML(p) {
  const l = _light(p.light);
  const t = esc(p.ticker);
  const reasons = Array.isArray(p.reasons) ? p.reasons : [];
  const showReasons = l !== 'xanh' && reasons.length;
  return `
    <article class="pl-card pl-card--${l}" data-plan-id="${Number(p.id)}" aria-labelledby="plT${Number(p.id)}">
      <header class="pl-card-head">
        <span class="pl-light pl-light--${l}" aria-hidden="true"></span>
        <div class="pl-card-id">
          <h3 class="pl-ticker" id="plT${Number(p.id)}">${t}</h3>
          <span class="pl-light-label">${LIGHTS[l].label}</span>
        </div>
        <div class="pl-price">
          <strong>${fmtVnd(p.price)}</strong>
          <span class="${signClass(p.pnl_pct)}">${_signedPct(p.pnl_pct)}</span>
        </div>
      </header>
      ${showReasons ? `<ul class="pl-reasons">${reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}
      ${_trackHTML(p)}
      ${_factsHTML(p)}
      <section class="pl-block" aria-label="Điều tôi tin khi mua">
        <h4 class="pl-block-h">Vì sao tôi mua</h4>
        ${_beliefsHTML(p.beliefs)}
      </section>
      ${_eventsHTML(p.events)}
      <footer class="pl-card-foot">
        <button type="button" class="ghost-btn pl-btn" data-plan-explain="${Number(p.id)}">Giải thích</button>
        <button type="button" class="ghost-btn pl-btn" data-plan-baserate="${t}">Base rate ${t}</button>
        ${p.checked_at ? `<span class="pl-checked">Kiểm lúc ${_time(p.checked_at)}</span>` : ''}
      </footer>
      <div class="pl-explain" id="plExplain${Number(p.id)}" hidden aria-live="polite"></div>
    </article>`;
}

function _time(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' });
}

function _countsHTML(plans) {
  const n = { do: 0, vang: 0, xanh: 0, xam: 0 };
  plans.forEach(p => { n[_light(p.light)]++; });
  return `<ul class="pl-counts" aria-label="Tổng số kế hoạch theo màu đèn">${
    ['do', 'vang', 'xanh', 'xam'].filter(k => n[k]).map(k =>
      `<li><span class="pl-light pl-light--${k}" aria-hidden="true"></span><strong>${n[k]}</strong> ${LIGHTS[k].label.toLowerCase()}</li>`,
    ).join('')}</ul>`;
}

function _guideHTML() {
  return `
    <details class="pl-guide">
      <summary>Kế hoạch nắm giữ là gì?</summary>
      <ol>
        <li><strong>Vì sao mua</strong> — chọn những điều bạn tin (giá rẻ, lợi nhuận tăng…). Hệ thống tự kiểm bằng số liệu sau mỗi phiên.</li>
        <li><strong>Sai ở đâu thì bán (Stop-loss)</strong> — mức giá mà nếu thủng, bạn thừa nhận mình sai.</li>
        <li><strong>Target</strong> — mức giá đủ để chốt lời hoặc nghĩ lại.</li>
        <li><strong>Khi nào xem lại</strong> — dù không có gì xảy ra, tới hạn thì xem lại.</li>
      </ol>
      <p>Gợi ý Stop-loss/Target lấy từ base rate: các lần trước đây chính mã đó ở trạng thái tương tự — không phải dự báo.</p>
    </details>`;
}

function _emptyHTML() {
  return `
    <div class="pl-state">
      <p class="pl-state-title">Chưa có kế hoạch nào.</p>
      <p class="pl-state-detail">Mỗi mã bạn nắm nên có một kế hoạch: vì sao mua, Stop-loss, Target và khi nào xem lại.</p>
      <button type="button" class="primary-btn" data-plan-new>+ Lập kế hoạch đầu tiên</button>
    </div>`;
}

function _errorHTML(msg) {
  return `
    <div class="pl-state pl-state--error" role="alert">
      <p class="pl-state-title">Không tải được kế hoạch.</p>
      <p class="pl-state-detail">${esc(msg)}</p>
      <button type="button" class="ghost-btn" data-plan-retry>Thử lại</button>
    </div>`;
}

function _skeletonHTML() {
  return `<div class="pl-grid" aria-busy="true">${[1, 2, 3].map(() =>
    '<div class="pl-card pl-card--skel"><span class="pl-skel" style="width:40%"></span><span class="pl-skel" style="width:85%"></span><span class="pl-skel" style="width:70%"></span></div>',
  ).join('')}</div>`;
}

// ── Explain ─────────────────────────────────────────────────────────────────
async function _explain(id, btn) {
  const box = document.getElementById(`plExplain${id}`);
  if (!box) return;
  if (!box.hidden && box.dataset.loaded) {
    box.hidden = true;
    btn.textContent = 'Giải thích';
    btn.setAttribute('aria-expanded', 'false');
    return;
  }
  box.hidden = false;
  btn.setAttribute('aria-expanded', 'true');
  if (box.dataset.loaded) { btn.textContent = 'Ẩn giải thích'; return; }
  btn.disabled = true;
  box.innerHTML = '<p class="pl-muted"><span class="pl-spinner" aria-hidden="true"></span>AI đang đọc số liệu của kế hoạch…</p>';
  try {
    const d = await sendJson(`${BASE}/${id}/explain`, 'POST', {});
    const list = (xs) => (Array.isArray(xs) && xs.length ? `<ul>${xs.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '');
    box.innerHTML = `
      ${d.tom_tat ? `<p class="pl-explain-lead">${esc(d.tom_tat)}</p>` : ''}
      ${d.vi_sao?.length ? `<h5>Vì sao</h5>${list(d.vi_sao)}` : ''}
      ${d.dieu_can_xem?.length ? `<h5>Điều nên xem</h5>${list(d.dieu_can_xem)}` : ''}
      <p class="pl-explain-note">${esc(d.luu_y || 'AI chỉ diễn giải số liệu đã tính, không phải lời khuyên mua bán.')}</p>`;
    box.dataset.loaded = '1';
    btn.textContent = 'Ẩn giải thích';
  } catch (err) {
    box.innerHTML = `<p class="pl-warn" role="alert">AI chưa giải thích được: ${esc(err?.message ?? '')}</p>`;
    btn.setAttribute('aria-expanded', 'false');
  } finally {
    btn.disabled = false;
  }
}

async function _checkNow(btn) {
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = 'Đang kiểm tra…';
  try {
    const r = await sendJson(`${BASE}/check`, 'POST', {});
    const changed = Array.isArray(r?.changed) ? r.changed : [];
    showToast(changed.length
      ? `Đèn đổi màu: ${changed.map(c => `${c.ticker} → ${LIGHTS[_light(c.to)].label.toLowerCase()}`).join(', ')}`
      : 'Đã kiểm tra — không kế hoạch nào đổi màu.');
    await loadPlans({ silent: true });
  } catch (err) {
    showToast(`Kiểm tra thất bại: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

// ── Public ──────────────────────────────────────────────────────────────────
export async function loadPlans({ silent = false } = {}) {
  const root = _root();
  if (!root) return;
  _bind();
  const seq = ++_seq;
  if (!silent || !_plans.length) root.innerHTML = _skeletonHTML();
  try {
    const data = await getJson(BASE);
    if (seq !== _seq) return;
    _plans = Array.isArray(data) ? data : [];
    _lights.clear();
    _plans.forEach(p => _lights.set(String(p.ticker).toUpperCase(), { light: _light(p.light), id: p.id }));
    document.dispatchEvent(new CustomEvent('plans:loaded', { detail: { count: _plans.length } }));
    root.innerHTML = _plans.length
      ? `${_countsHTML(_plans)}<div class="pl-grid">${_plans.map(_cardHTML).join('')}</div>${_guideHTML()}`
      : _emptyHTML() + _guideHTML();
  } catch (err) {
    if (seq !== _seq) return;
    if (silent && _plans.length) return;
    root.innerHTML = _errorHTML(err?.message ?? 'Lỗi không xác định');
  }
}

function _bind() {
  if (_bound) return;
  _bound = true;
  document.addEventListener('click', (e) => {
    const nw = e.target.closest('[data-plan-new]');
    if (nw) { openPlanForm({ ticker: nw.dataset.planNew || '' }); return; }
    const check = e.target.closest('[data-plan-check]');
    if (check) { _checkNow(check); return; }
    if (!e.target.closest('#plansRoot')) return;
    if (e.target.closest('[data-plan-retry]')) { loadPlans(); return; }
    const ex = e.target.closest('[data-plan-explain]');
    if (ex) { _explain(Number(ex.dataset.planExplain), ex); return; }
    const br = e.target.closest('[data-plan-baserate]');
    if (br) openBaserateDrawer(br.dataset.planBaserate, { inPortfolio: true });
  });
  document.addEventListener('plan:created', () => loadPlans({ silent: true }));
}
