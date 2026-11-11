/**
 * plan-form.js — Form "Lập kế hoạch nắm giữ": 4 câu hỏi, có gợi ý từ tiền lệ.
 * Owner: modules/plans
 *
 *   1. Mã nào, mua giá bao nhiêu?
 *   2. Vì sao tôi mua?        — chọn điều hệ thống tự kiểm được + điều tự theo dõi
 *   3. Sai ở đâu thì bán? Mục tiêu?  — gợi ý từ mức sụt / lãi thường gặp của chính mã
 *   4. Khi nào xem lại?        — mặc định ~6 tháng, kèm sự kiện đang chờ (tuỳ chọn)
 *
 * Nguồn: GET /api/v1/plans/suggest?ticker=&entry=  ·  POST /api/v1/plans
 * Exports: openPlanForm({ ticker })
 */

import { getJson, sendJson } from '../../api/client.js?v=1';
import { esc } from '../../utils/format.js?v=1';
import { showToast } from '../../utils/dom.js?v=1';
import { fmtVnd } from '../board/baserate-drawer.js?v=2';

const BASE = '/api/v1/plans';
const DIALOG_ID = 'planModal';
const NOW = Object.freeze({
  dung:    { cls: 'pf-now--yes', text: 'Hiện đang đúng' },
  sai:     { cls: 'pf-now--no',  text: 'Hiện chưa đúng' },
  chua_ro: { cls: 'pf-now--na',  text: 'Chưa đủ số liệu' },
});

let _suggest = null;
let _suggestSeq = 0;
let _timer = null;

/** "422 {"detail":"…"}" → "…" */
function _apiMsg(err) {
  const raw = String(err?.message ?? '');
  const m = raw.match(/^\d{3}\s+(.*)$/s);
  if (!m) return raw;
  try {
    const d = JSON.parse(m[1]).detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map(x => x.msg).join('; ');
  } catch { /* không phải JSON */ }
  return m[1];
}

function _num(id) {
  const v = parseFloat(String(document.getElementById(id)?.value ?? '').replace(/[.\s]/g, '').replace(',', '.'));
  return Number.isFinite(v) && v > 0 ? v : null;
}

function _ensureDialog() {
  let d = document.getElementById(DIALOG_ID);
  if (d) return d;
  d = document.createElement('dialog');
  d.id = DIALOG_ID;
  d.className = 'pf-dialog';
  d.setAttribute('aria-labelledby', 'pfTitle');
  d.innerHTML = `
    <div class="modal-inner">
      <div class="modal-header">
        <h2 id="pfTitle">Lập kế hoạch nắm giữ</h2>
        <button type="button" class="modal-close" data-pf-close aria-label="Đóng">&#x2715;</button>
      </div>
      <form id="pfForm" class="pf-form" novalidate>
        <fieldset class="pf-step">
          <legend><span class="pf-num">1</span>Mã nào, Entry bao nhiêu?</legend>
          <div class="pf-row">
            <label class="form-field">
              <span>Mã cổ phiếu</span>
              <input id="pfTicker" class="ticker-input" autocomplete="off" placeholder="VD: FPT" maxlength="10" required />
            </label>
            <label class="form-field">
              <span>Entry (đ)</span>
              <input id="pfEntry" inputmode="decimal" placeholder="để trống = giá hiện tại" />
            </label>
          </div>
          <p class="pf-hint" id="pfTickerHint" aria-live="polite">Nhập mã để xem gợi ý từ lịch sử của chính mã đó.</p>
        </fieldset>

        <fieldset class="pf-step">
          <legend><span class="pf-num">2</span>Vì sao tôi mua?</legend>
          <p class="pf-hint">Chọn những điều bạn đang tin. Hệ thống kiểm lại bằng số liệu sau mỗi phiên — điều nào hết đúng, đèn sẽ đổi màu.</p>
          <div class="pf-beliefs" id="pfBeliefs"><p class="pf-muted">Nhập mã ở bước 1 để xem.</p></div>
          <label class="form-field">
            <span>Điều khác bạn tự theo dõi <span class="pf-muted">(mỗi dòng một điều, tuỳ chọn)</span></span>
            <textarea id="pfOwn" rows="2" maxlength="1000" placeholder="VD: Giá thép phục hồi sau Tết"></textarea>
          </label>
        </fieldset>

        <fieldset class="pf-step">
          <legend><span class="pf-num">3</span>Sai ở đâu thì bán? Chốt lời ở đâu? <span class="pf-muted">(Stop-loss · Target)</span></legend>
          <div class="pf-row">
            <label class="form-field">
              <span>Stop-loss (đ)</span>
              <input id="pfStop" inputmode="decimal" placeholder="—" />
              <span class="pf-suggest" id="pfStopNote"></span>
            </label>
            <label class="form-field">
              <span>Target (đ)</span>
              <input id="pfTarget" inputmode="decimal" placeholder="—" />
              <span class="pf-suggest" id="pfTargetNote"></span>
            </label>
          </div>
        </fieldset>

        <fieldset class="pf-step">
          <legend><span class="pf-num">4</span>Khi nào xem lại?</legend>
          <div class="pf-row">
            <label class="form-field">
              <span>Ngày xem lại</span>
              <input id="pfReview" type="date" />
            </label>
            <label class="form-field">
              <span>Sự kiện đang chờ <span class="pf-muted">(tuỳ chọn)</span></span>
              <input id="pfEventText" maxlength="500" placeholder="VD: BCTC quý 4" />
            </label>
            <label class="form-field">
              <span>Ngày dự kiến</span>
              <input id="pfEventDate" type="date" />
            </label>
          </div>
        </fieldset>

        <p class="pf-error" id="pfError" role="alert" hidden></p>
        <div class="modal-footer">
          <button type="button" class="ghost-btn" data-pf-close>Huỷ</button>
          <button type="submit" class="primary-btn" id="pfSubmit">Lưu kế hoạch</button>
        </div>
      </form>
    </div>`;
  document.body.appendChild(d);

  d.addEventListener('click', (e) => {
    if (e.target.closest('[data-pf-close]')) d.close();
    const use = e.target.closest('[data-pf-use]');
    if (use) {
      const input = document.getElementById(use.dataset.pfUse);
      if (input) input.value = use.dataset.value;
    }
  });
  d.querySelector('#pfTicker').addEventListener('input', () => _scheduleSuggest());
  d.querySelector('#pfEntry').addEventListener('input', () => _scheduleSuggest());
  d.querySelector('#pfForm').addEventListener('submit', (e) => { e.preventDefault(); _submit(); });
  return d;
}

function _scheduleSuggest() {
  clearTimeout(_timer);
  _timer = setTimeout(_loadSuggest, 450);
}

function _setNote(id, inputId, value, note) {
  const el = document.getElementById(id);
  if (!el) return;
  if (value == null) { el.innerHTML = ''; return; }
  el.innerHTML = `Gợi ý <button type="button" class="pf-use" data-pf-use="${inputId}" data-value="${Math.round(value)}">${fmtVnd(value)}</button>${note ? ` — ${esc(note)}` : ''}`;
}

function _renderBeliefs(templates) {
  const wrap = document.getElementById('pfBeliefs');
  if (!wrap) return;
  const checked = new Set([...wrap.querySelectorAll('input:checked')].map(i => i.value));
  wrap.innerHTML = templates.map(t => {
    const now = NOW[t.now] ?? null;
    return `<label class="pf-belief">
      <input type="checkbox" value="${esc(t.key)}" data-label="${esc(t.label)}" ${checked.has(t.key) ? 'checked' : ''} />
      <span class="pf-belief-body">
        <span class="pf-belief-label">${esc(t.label)}</span>
        <span class="pf-belief-explain">${esc(t.explain)}</span>
      </span>
      ${now ? `<span class="pf-now ${now.cls}">${now.text}</span>` : ''}
    </label>`;
  }).join('');
}

async function _loadSuggest() {
  const ticker = String(document.getElementById('pfTicker')?.value ?? '').trim().toUpperCase();
  const hint = document.getElementById('pfTickerHint');
  if (!ticker) { hint.textContent = 'Nhập mã để xem gợi ý từ lịch sử của chính mã đó.'; return; }
  const seq = ++_suggestSeq;
  hint.textContent = `Đang xem lịch sử ${ticker}…`;
  const qs = new URLSearchParams({ ticker });
  const entry = _num('pfEntry');
  if (entry) qs.set('entry', String(entry));
  try {
    const s = await getJson(`${BASE}/suggest?${qs}`);
    if (seq !== _suggestSeq) return;
    _suggest = s;
    hint.textContent = s.episodes
      ? `${ticker}: gợi ý từ base rate: ${s.episodes} lần trước đây mã ở trạng thái tương tự.`
      : `${ticker}: chưa đủ base rate — gợi ý Stop-loss/Target chỉ mang tính tham khảo.`;
    const entryEl = document.getElementById('pfEntry');
    if (entryEl && !entryEl.value && s.entry_price) entryEl.placeholder = `hiện tại ${fmtVnd(s.entry_price)}`;
    _renderBeliefs(Array.isArray(s.templates) ? s.templates : []);
    _setNote('pfStopNote', 'pfStop', s.stop_price, s.stop_note);
    _setNote('pfTargetNote', 'pfTarget', s.target_price, s.target_note);
    const review = document.getElementById('pfReview');
    if (review && !review.value && s.review_by) review.value = s.review_by;
  } catch (err) {
    if (seq !== _suggestSeq) return;
    _suggest = null;
    hint.textContent = String(err?.message ?? '').startsWith('404')
      ? `Không có mã ${ticker}.`
      : `Chưa lấy được gợi ý: ${_apiMsg(err)}`;
  }
}

function _showError(msg) {
  const e = document.getElementById('pfError');
  e.textContent = msg;
  e.hidden = !msg;
}

async function _submit() {
  const ticker = String(document.getElementById('pfTicker').value || '').trim().toUpperCase();
  if (!ticker) { _showError('Nhập mã cổ phiếu.'); return; }
  const entry = _num('pfEntry') ?? _suggest?.entry_price ?? null;
  const stop = _num('pfStop');
  const target = _num('pfTarget');
  if (stop && entry && stop >= entry) { _showError('Stop-loss phải thấp hơn Entry.'); return; }
  if (target && entry && target <= entry) { _showError('Target nên cao hơn Entry.'); return; }
  const beliefs = [...document.querySelectorAll('#pfBeliefs input:checked')]
    .map(i => ({ text: i.dataset.label, check_key: i.value }));
  String(document.getElementById('pfOwn').value || '').split('\n').map(s => s.trim()).filter(Boolean)
    .slice(0, 5).forEach(text => beliefs.push({ text, check_key: null }));
  if (!beliefs.length) { _showError('Chọn hoặc ghi ít nhất một lý do mua.'); return; }
  const evText = String(document.getElementById('pfEventText').value || '').trim();
  const evDate = document.getElementById('pfEventDate').value || null;
  const body = {
    ticker,
    entry_price: entry,
    stop_price: stop,
    target_price: target,
    review_by: document.getElementById('pfReview').value || null,
    beliefs,
    events: evText ? [{ text: evText, expected_date: evDate }] : [],
  };
  const btn = document.getElementById('pfSubmit');
  btn.disabled = true;
  btn.textContent = 'Đang lưu…';
  _showError('');
  try {
    await sendJson(BASE, 'POST', body);
    document.getElementById(DIALOG_ID).close();
    showToast(`Đã lưu kế hoạch ${ticker}`);
    document.dispatchEvent(new CustomEvent('plan:created', { detail: { ticker } }));
  } catch (err) {
    _showError(`Chưa lưu được: ${_apiMsg(err)}`);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Lưu kế hoạch';
  }
}

export function openPlanForm({ ticker = '' } = {}) {
  const d = _ensureDialog();
  d.querySelector('#pfForm').reset();
  _suggest = null;
  _showError('');
  document.getElementById('pfBeliefs').innerHTML = '<p class="pf-muted">Nhập mã ở bước 1 để xem.</p>';
  ['pfStopNote', 'pfTargetNote'].forEach(id => { document.getElementById(id).innerHTML = ''; });
  document.getElementById('pfEntry').placeholder = 'để trống = giá hiện tại';
  document.getElementById('pfTicker').value = ticker ? String(ticker).toUpperCase() : '';
  d.showModal();
  if (ticker) _loadSuggest(); else document.getElementById('pfTicker').focus();
}
