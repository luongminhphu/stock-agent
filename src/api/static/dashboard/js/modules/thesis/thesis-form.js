/**
 * thesis-form.js
 * Owner: modules/thesis
 * Responsibility: DOM form builders, form collectors, modal openers, event wiring.
 * - makeAssumptionRow / makeCatalystRow
 * - clearFormRows / seedBlankFormRows
 * - collectFormAssumptions / collectFormCatalysts
 * - syncNewDetailItems
 * - openNewThesisModal / openEditThesisModal
 * - openAssumptionModal / openCatalystModal
 * - wireDetailActions (bind events inside detail panel)
 * - bindThesisFormEvents (bind global form submit + delete confirm)
 */

import { el, showToast, openModal, closeModal } from '../../utils/dom.js?v=1';
import { esc } from '../../utils/format.js?v=1';
import { thesisApiBase, getJson, sendJson } from '../../api/client.js?v=1';
import { state } from '../../state/dashboard-state.js?v=1';
import { icon as ic } from '../../utils/icons.js?v=2';
import {
  confirmDeleteThesis,
  confirmDeleteAssumption,
  confirmDeleteCatalyst,
  loadThesisDetail,
  triggerAiReview,
  openApplyAiReviewModal,
} from './thesis-service.js?v=1';

// ---------------------------------------------------------------------------
// Form row builders
// ---------------------------------------------------------------------------
export function makeAssumptionRow(data = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'detail-item form-row-item';
  wrap.innerHTML = `
    <div class="form-field" style="flex:1;">
      <label>Assumption</label>
      <textarea class="form-assumption-description" placeholder="Nội dung assumption">${esc(data.description)}</textarea>
    </div>
    <div class="form-field" style="flex:1;">
      <label>Rationale</label>
      <textarea class="form-assumption-rationale" placeholder="Cơ sở / logic">${esc(data.rationale)}</textarea>
    </div>
    <div style="display:flex;align-items:flex-end;">
      <button type="button" class="icon-btn danger remove-form-row-btn" title="Xóa dòng">${ic('trash', { size: 14 })}</button>
    </div>`;
  wrap.querySelector('.remove-form-row-btn').addEventListener('click', () => wrap.remove());
  return wrap;
}

export function makeCatalystRow(data = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'detail-item form-row-item';
  wrap.innerHTML = `
    <div class="form-field" style="flex:1;">
      <label>Catalyst</label>
      <textarea class="form-catalyst-description" placeholder="Mô tả catalyst">${esc(data.description)}</textarea>
    </div>
    <div class="form-field" style="flex:1;">
      <label>Rationale</label>
      <textarea class="form-catalyst-rationale" placeholder="Tác động kỳ vọng">${esc(data.rationale)}</textarea>
    </div>
    <div class="form-field" style="min-width:180px;">
      <label>Ngày kỳ vọng</label>
      <input type="date" class="form-catalyst-date" value="${data.expected_date ? data.expected_date.slice(0,10) : ''}" />
    </div>
    <div style="display:flex;align-items:flex-end;">
      <button type="button" class="icon-btn danger remove-form-row-btn" title="Xóa dòng">${ic('trash', { size: 14 })}</button>
    </div>`;
  wrap.querySelector('.remove-form-row-btn').addEventListener('click', () => wrap.remove());
  return wrap;
}

// ---------------------------------------------------------------------------
// Form row helpers
// ---------------------------------------------------------------------------
export function clearFormRows() {
  const a = el('thesisFormAssumptionRows');
  const c = el('thesisFormCatalystRows');
  if (a) a.innerHTML = '';
  if (c) c.innerHTML = '';
}

export function seedBlankFormRows() {
  const a = el('thesisFormAssumptionRows');
  const c = el('thesisFormCatalystRows');
  if (a && !a.children.length) a.appendChild(makeAssumptionRow());
  if (c && !c.children.length) c.appendChild(makeCatalystRow());
}

export function collectFormAssumptions() {
  return Array.from(document.querySelectorAll('#thesisFormAssumptionRows .form-row-item'))
    .map(row => ({
      description: row.querySelector('.form-assumption-description')?.value?.trim() || '',
      rationale:   row.querySelector('.form-assumption-rationale')?.value?.trim()   || null,
    }))
    .filter(x => x.description);
}

export function collectFormCatalysts() {
  return Array.from(document.querySelectorAll('#thesisFormCatalystRows .form-row-item'))
    .map(row => ({
      description:  row.querySelector('.form-catalyst-description')?.value?.trim() || '',
      rationale:    row.querySelector('.form-catalyst-rationale')?.value?.trim()   || null,
      expected_date: row.querySelector('.form-catalyst-date')?.value || null,
    }))
    .filter(x => x.description);
}

// ---------------------------------------------------------------------------
// Sync new items (create only, no duplicate)
// ---------------------------------------------------------------------------
export async function syncNewDetailItems(thesisId, assumptions, catalysts) {
  const [existingAssums, existingCats] = await Promise.all([
    getJson(`${thesisApiBase()}/${thesisId}/assumptions`).catch(() => []),
    getJson(`${thesisApiBase()}/${thesisId}/catalysts`).catch(() => []),
  ]);
  const assumList = Array.isArray(existingAssums) ? existingAssums : (existingAssums?.items ?? []);
  const catList   = Array.isArray(existingCats)   ? existingCats   : (existingCats?.items   ?? []);
  const existingAssumDescs = new Set(assumList.map(a => (a.description ?? '').trim()));
  const existingCatDescs   = new Set(catList.map(c   => (c.description ?? '').trim()));

  for (const a of assumptions) {
    if (!existingAssumDescs.has(a.description)) {
      await sendJson(`${thesisApiBase()}/${thesisId}/assumptions`, 'POST', { ...a, status: 'pending', confidence: null });
    }
  }
  for (const c of catalysts) {
    if (!existingCatDescs.has(c.description)) {
      await sendJson(`${thesisApiBase()}/${thesisId}/catalysts`, 'POST', { ...c, status: 'pending' });
    }
  }
}

// ---------------------------------------------------------------------------
// Modal openers
// ---------------------------------------------------------------------------
export function openNewThesisModal() {
  el('thesisModalTitle').textContent = 'Tạo Thesis mới';
  el('thesisIdField').value = '';
  el('thesisForm').reset();
  clearFormRows();
  seedBlankFormRows();
  el('suggestResult').classList.add('hidden');
  el('suggestLoading').classList.add('hidden');
  openModal('thesisModal');
}

export async function openEditThesisModal(thesisId) {
  el('thesisModalTitle').textContent = 'Chỉnh sửa Thesis';
  el('suggestResult').classList.add('hidden');
  el('suggestLoading').classList.add('hidden');
  try {
    const [t, assumptions, catalysts] = await Promise.all([
      getJson(`${thesisApiBase()}/${thesisId}`),
      getJson(`${thesisApiBase()}/${thesisId}/assumptions`).catch(() => []),
      getJson(`${thesisApiBase()}/${thesisId}/catalysts`).catch(() => []),
    ]);
    el('thesisIdField').value        = t.id;
    el('thesisTickerField').value    = t.ticker ?? '';
    el('thesisTitleField').value     = t.title ?? '';
    el('thesisSummaryField').value   = t.summary ?? '';
    el('thesisEntryField').value     = t.entry_price ?? '';
    el('thesisTargetField').value    = t.target_price ?? '';
    el('thesisStopField').value      = t.stop_loss ?? '';
    el('thesisStatusField').value    = t.status ?? 'active';
    el('thesisDirectionField').value = t.direction ?? '';  // fix: was ?? 'bullish' — invalid enum fallback
    el('suggestTicker').value        = t.ticker ?? '';
    clearFormRows();
    const aWrap   = el('thesisFormAssumptionRows');
    const cWrap   = el('thesisFormCatalystRows');
    const assumList = Array.isArray(assumptions) ? assumptions : (assumptions?.items ?? []);
    const catList   = Array.isArray(catalysts)   ? catalysts   : (catalysts?.items   ?? []);
    assumList.forEach(a => aWrap?.appendChild(makeAssumptionRow(a)));
    catList.forEach(c   => cWrap?.appendChild(makeCatalystRow(c)));
    seedBlankFormRows();
    openModal('thesisModal');
  } catch (err) {
    showToast(`Không tải được thesis: ${err.message}`, 'error');
  }
}

/**
 * Mã cổ phiếu của thesis: ưu tiên mã truyền vào (thesis đang mở), rồi danh sách đã tải,
 * cuối cùng hỏi API — thesis mở từ tab Kế hoạch / bộ lọc khác không nằm trong state.theses.
 */
async function _resolveTicker(thesisId, ticker) {
  if (ticker) return String(ticker).toUpperCase();
  const known = state.theses.find(t => String(t.id ?? t.thesis_id) === String(thesisId))?.ticker;
  if (known) return String(known).toUpperCase();
  try {
    const t = await getJson(`${thesisApiBase()}/${thesisId}`);
    return t?.ticker ? String(t.ticker).toUpperCase() : '';
  } catch {
    return '';
  }
}

export async function openAssumptionModal(thesisId, assumId, knownTicker = '') {
  const ticker = await _resolveTicker(thesisId, knownTicker);
  // fix: reset() BEFORE setting hidden fields — both inputs are inside <form>,
  // calling reset() after would wipe thesisId/assumId and break the submit URL.
  el('assumptionForm').reset();
  el('assumptionThesisId').value = thesisId;
  el('assumptionIdField').value  = assumId ?? '';
  el('assumptionModalTitle').textContent = assumId ? 'Chỉnh sửa Assumption' : 'Thêm Assumption';
  el('assumptionSuggestTicker').value = ticker;
  if (el('assumptionTickerDisplay')) el('assumptionTickerDisplay').textContent = ticker || '—';
  el('assumptionSuggestResult').classList.add('hidden');
  el('assumptionSuggestLoading').classList.add('hidden');
  if (assumId) {
    try {
      const a = await getJson(`${thesisApiBase()}/${thesisId}/assumptions/${assumId}`);
      el('assumptionDescField').value       = a.description ?? '';
      el('assumptionRationaleField').value  = a.rationale ?? '';
      el('assumptionStatusField').value     = a.status ?? 'valid';
      el('assumptionConfidenceField').value = a.confidence ?? '';
    } catch (err) {
      showToast(`Không tải được assumption: ${err.message}`, 'error');
      return;
    }
  }
  openModal('assumptionModal');
}

export async function openCatalystModal(thesisId, catId, knownTicker = '') {
  const ticker = await _resolveTicker(thesisId, knownTicker);
  // fix: reset() BEFORE setting hidden fields — both inputs are inside <form>,
  // calling reset() after would wipe thesisId/catId and break the submit URL.
  el('catalystForm').reset();
  el('catalystThesisId').value = thesisId;
  el('catalystIdField').value  = catId ?? '';
  el('catalystModalTitle').textContent = catId ? 'Chỉnh sửa Catalyst' : 'Thêm Catalyst';
  el('catalystSuggestTicker').value = ticker;
  if (el('catalystTickerDisplay')) el('catalystTickerDisplay').textContent = ticker || '—';
  el('catalystSuggestResult').classList.add('hidden');
  el('catalystSuggestLoading').classList.add('hidden');
  if (catId) {
    try {
      const c = await getJson(`${thesisApiBase()}/${thesisId}/catalysts/${catId}`);
      el('catalystDescField').value      = c.description ?? '';
      el('catalystRationaleField').value = c.rationale ?? '';
      el('catalystStatusField').value    = c.status ?? 'pending';
      el('catalystDateField').value  = c.expected_date ? c.expected_date.slice(0, 10) : '';
    } catch (err) {
      showToast(`Không tải được catalyst: ${err.message}`, 'error');
      return;
    }
  }
  openModal('catalystModal');
}

// ---------------------------------------------------------------------------
// wireDetailActions — bind events bên trong detail panel sau khi render
// ---------------------------------------------------------------------------
export function wireDetailActions(thesisId, wrap, ticker = '') {
  wrap.querySelector('#detailEditBtn')?.addEventListener('click', () => openEditThesisModal(thesisId));
  wrap.querySelector('#detailDeleteBtn')?.addEventListener('click', () => confirmDeleteThesis(thesisId));
  wrap.querySelector('#detailCloseBtn')?.addEventListener('click', () => _confirmLifecycle(thesisId, 'close'));
  wrap.querySelector('#detailInvalidateBtn')?.addEventListener('click', () => _confirmLifecycle(thesisId, 'invalidate'));
  wrap.querySelector('#detailReactivateBtn')?.addEventListener('click', () => _confirmLifecycle(thesisId, 'reactivate'));
  wrap.querySelector('#addAssumBtn')?.addEventListener('click', () => openAssumptionModal(thesisId, null, ticker));
  wrap.querySelectorAll('.edit-assum-btn').forEach(btn =>
    btn.addEventListener('click', () => openAssumptionModal(thesisId, btn.dataset.id, ticker)));
  wrap.querySelectorAll('.delete-assum-btn').forEach(btn =>
    btn.addEventListener('click', () => confirmDeleteAssumption(thesisId, btn.dataset.id)));
  wrap.querySelector('#addCatBtn')?.addEventListener('click', () => openCatalystModal(thesisId, null, ticker));
  wrap.querySelectorAll('.edit-cat-btn').forEach(btn =>
    btn.addEventListener('click', () => openCatalystModal(thesisId, btn.dataset.id, ticker)));
  wrap.querySelectorAll('.delete-cat-btn').forEach(btn =>
    btn.addEventListener('click', () => confirmDeleteCatalyst(thesisId, btn.dataset.id)));
  wrap.querySelector(`#aiReviewBtn-${thesisId}`)?.addEventListener('click', () => triggerAiReview(thesisId));

  wrap.addEventListener('click', async (e) => {
    if (e.target.closest('.apply-ai-review-btn')) {
      const tid = e.target.closest('.apply-ai-review-btn').dataset.thesisId;
      openApplyAiReviewModal(Number(tid));
      return;
    }
    if (e.target.closest('.dismiss-ai-review-btn')) {
      const tid = e.target.closest('.dismiss-ai-review-btn').dataset.thesisId;
      const r = wrap.querySelector(`#aiReviewResult-${tid}`);
      if (r) { r.classList.add('hidden'); r.innerHTML = ''; }
      return;
    }
  });
}

// ---------------------------------------------------------------------------
// Thesis lifecycle: Close + Invalidate
// ---------------------------------------------------------------------------

/**
 * _confirmLifecycle — hiện confirm dialog trước khi POST /close, /invalidate hoặc /reactivate (T1).
 * @param {number} thesisId
 * @param {'close'|'invalidate'|'reactivate'} action
 */
const _LIFECYCLE = {
  close:      { label: 'đóng',       done: 'đóng',       icon: 'check-circle',   title: 'Xác nhận đóng thesis',   btn: 'Đóng thesis', danger: true,  extra: '' },
  invalidate: { label: 'invalidate', done: 'invalidate', icon: 'alert-triangle', title: 'Xác nhận invalidate',    btn: 'Invalidate',  danger: true,  extra: '' },
  reactivate: { label: 'mở lại',     done: 'mở lại',     icon: 'rotate-ccw',     title: 'Xác nhận mở lại thesis', btn: 'Mở lại',      danger: false,
                extra: ' Thesis sẽ quay lại ACTIVE, giữ nguyên lịch sử review và quyết định; watchdog và review AI tiếp tục theo dõi.' },
};
async function _confirmLifecycle(thesisId, action) {
  const meta     = _LIFECYCLE[action] ?? _LIFECYCLE.close;
  const label    = meta.label;
  const idNum    = Number(thesisId);
  const msgEl    = el('deleteModalMsg');
  // state.theses chỉ chứa thesis đang active (bảng dashboard) — thesis đã đóng/paused
  // lấy mã từ thanh detail đang mở.
  const t        = state.theses?.find(x => Number(x.id) === idNum);
  const ticker   = t?.ticker ?? el('thesisDetail')?.querySelector('.dsb-ticker')?.textContent?.trim() ?? '';
  const name     = t?.title ? `"${esc(t.title)}"` : `#${esc(String(thesisId))}`;
  if (msgEl) {
    // innerHTML: icon là SVG nội bộ; dữ liệu thesis đi qua esc()
    msgEl.innerHTML = `${ic(meta.icon)} Bạn chắc chắn muốn ${label} thesis ${name}${ticker ? ` (${esc(ticker)})` : ''}?${meta.extra}`;
  }
  // Modal dùng chung với Xóa — đặt tiêu đề / nút theo hành động, trả về mặc định khi đóng
  const dlg = el('deleteModal'), titleEl = el('deleteModalTitle'), btnEl = el('deleteConfirmBtn');
  if (titleEl) titleEl.textContent = meta.title;
  if (btnEl) { btnEl.textContent = meta.btn; btnEl.className = meta.danger ? 'danger-btn' : 'primary-btn'; }
  dlg?.addEventListener('close', () => {
    if (titleEl) titleEl.textContent = 'Xác nhận xóa';
    if (btnEl) { btnEl.textContent = 'Xóa'; btnEl.className = 'danger-btn'; }
  }, { once: true });
  state.deleteCallback = async () => {
    try {
      await sendJson(`${thesisApiBase()}/${thesisId}/${action}`, 'POST', null);
      closeModal('deleteModal');
      showToast(`Thesis đã được ${meta.done}`);
      // Reload detail to reflect new status + refresh thesis list
      await loadThesisDetail(thesisId);
      document.dispatchEvent(new CustomEvent('thesis:lifecycle-changed', { detail: { thesisId, action } }));
    } catch (err) {
      closeModal('deleteModal');
      showToast(`Lỗi: ${err.message}`, 'error');
    }
  };
  openModal('deleteModal');
}

// ---------------------------------------------------------------------------
// bindThesisFormEvents — đăng ký form submit + global delete confirm
// Gọi 1 lần từ app.js khi DOMContentLoaded
// ---------------------------------------------------------------------------
let _thesisFormBound = false;  // guard: chống double-bind nếu hàm bị gọi lại

export function bindThesisFormEvents({ onThesisSaved } = {}) {
  if (_thesisFormBound) return;
  _thesisFormBound = true;

  // Thesis form submit
  el('thesisForm')?.addEventListener('submit', async e => {
    e.preventDefault();
    const btn = el('thesisSubmitBtn');

    // Guard: title bắt buộc
    const title = el('thesisTitleField')?.value?.trim() ?? '';
    if (!title) {
      showToast('Vui lòng nhập tên thesis', 'error');
      return;
    }

    // Guard: disable ngay lập tức (sync) trước mọi await — chặn double-click / Enter liên tiếp
    if (btn.disabled) return;
    btn.disabled = true;
    btn.classList.add('btn-loading');
    btn.textContent = 'Đang lưu…';

    const id = el('thesisIdField').value;
    const payload = {
      ticker:       el('thesisTickerField').value.trim().toUpperCase(),
      title,
      summary:      el('thesisSummaryField').value.trim() || null,
      entry_price:  el('thesisEntryField').value  ? Number(el('thesisEntryField').value)  : null,
      target_price: el('thesisTargetField').value ? Number(el('thesisTargetField').value) : null,
      stop_loss:    el('thesisStopField').value   ? Number(el('thesisStopField').value)   : null,
      direction:    el('thesisDirectionField').value || null,
    };
    const assumptions = collectFormAssumptions();
    const catalysts   = collectFormCatalysts();
    try {
      let thesisId = id;
      if (id) {
        await sendJson(`${thesisApiBase()}/${id}`, 'PATCH', payload);
        await syncNewDetailItems(id, assumptions, catalysts);
      } else {
        const created = await sendJson(thesisApiBase(), 'POST', {
          ...payload,
          assumptions: assumptions.map(a => a.description),
          catalysts:   catalysts.map(c => c.description),
        });
        thesisId = created?.id;
      }
      closeModal('thesisModal');
      if (typeof onThesisSaved === 'function') onThesisSaved(thesisId);
    } catch (err) {
      showToast(`Lưu thesis thất bại: ${err.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.classList.remove('btn-loading');
      btn.textContent = 'Lưu';
    }
  });

  // ---------------------------------------------------------------------------
  // Catalyst modal form submit (create + edit)
  // ---------------------------------------------------------------------------
  el('catalystForm')?.addEventListener('submit', async e => {
    e.preventDefault();
    const btn = e.submitter ?? el('catalystSubmitBtn');
    const thesisId = el('catalystThesisId').value;
    const catId    = el('catalystIdField').value;
    const payload  = {
      description:   el('catalystDescField').value.trim(),
      rationale:     el('catalystRationaleField').value.trim() || null,
      status:        el('catalystStatusField').value,
      expected_date: el('catalystDateField').value || null,
    };
    if (!payload.description) { showToast('Nhập mô tả catalyst', 'error'); return; }
    if (btn) { btn.disabled = true; btn.textContent = 'Đang lưu…'; }
    try {
      if (catId) {
        await sendJson(`${thesisApiBase()}/${thesisId}/catalysts/${catId}`, 'PATCH', payload);
      } else {
        await sendJson(`${thesisApiBase()}/${thesisId}/catalysts`, 'POST', payload);
      }
      closeModal('catalystModal');
      showToast('Đã lưu catalyst');
      await loadThesisDetail(Number(thesisId));
    } catch (err) {
      showToast(`Lưu catalyst thất bại: ${err.message}`, 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Lưu Catalyst'; }
    }
  });

  // ---------------------------------------------------------------------------
  // Assumption modal form submit (create + edit)
  // ---------------------------------------------------------------------------
  el('assumptionForm')?.addEventListener('submit', async e => {
    e.preventDefault();
    const btn = e.submitter ?? el('assumptionSubmitBtn');
    const thesisId = el('assumptionThesisId').value;
    const assumId  = el('assumptionIdField').value;
    const payload  = {
      description: el('assumptionDescField').value.trim(),
      rationale:   el('assumptionRationaleField').value.trim() || null,
      status:      el('assumptionStatusField').value,
      confidence:  el('assumptionConfidenceField').value ? Number(el('assumptionConfidenceField').value) : null,
    };
    if (!payload.description) { showToast('Nhập nội dung assumption', 'error'); return; }
    if (btn) { btn.disabled = true; btn.textContent = 'Đang lưu…'; }
    try {
      if (assumId) {
        await sendJson(`${thesisApiBase()}/${thesisId}/assumptions/${assumId}`, 'PATCH', payload);
      } else {
        await sendJson(`${thesisApiBase()}/${thesisId}/assumptions`, 'POST', payload);
      }
      closeModal('assumptionModal');
      showToast('Đã lưu assumption');
      await loadThesisDetail(Number(thesisId));
    } catch (err) {
      showToast(`Lưu assumption thất bại: ${err.message}`, 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Lưu Assumption'; }
    }
  });
}
