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

import { el, showToast, openModal, closeModal } from '../../utils/dom.js';
import { esc } from '../../utils/format.js';
import { thesisApiBase, getJson, sendJson } from '../../api/client.js';
import { state } from '../../state/dashboard-state.js';
import {
  confirmDeleteThesis,
  confirmDeleteAssumption,
  confirmDeleteCatalyst,
  loadThesisDetail,
  triggerAiReview,
  openApplyAiReviewModal,
} from './thesis-service.js';

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
      <button type="button" class="icon-btn danger remove-form-row-btn" title="Xóa dòng">🗑</button>
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
      <label>Timeline</label>
      <input class="form-catalyst-timeline" placeholder="Q3 2025" value="${esc(data.expected_timeline)}" />
    </div>
    <div style="display:flex;align-items:flex-end;">
      <button type="button" class="icon-btn danger remove-form-row-btn" title="Xóa dòng">🗑</button>
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
      description:       row.querySelector('.form-catalyst-description')?.value?.trim() || '',
      rationale:         row.querySelector('.form-catalyst-rationale')?.value?.trim()   || null,
      expected_timeline: row.querySelector('.form-catalyst-timeline')?.value?.trim()    || null,
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
    el('thesisDirectionField').value = t.direction ?? 'bullish';
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

export async function openAssumptionModal(thesisId, assumId) {
  const ticker = state.theses.find(t => String(t.id) === String(thesisId))?.ticker ?? '';
  el('assumptionThesisId').value = thesisId;
  el('assumptionIdField').value  = assumId ?? '';
  el('assumptionModalTitle').textContent = assumId ? 'Chỉnh sửa Assumption' : 'Thêm Assumption';
  el('assumptionForm').reset();
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

export async function openCatalystModal(thesisId, catId) {
  const ticker = state.theses.find(t => String(t.id) === String(thesisId))?.ticker ?? '';
  el('catalystThesisId').value = thesisId;
  el('catalystIdField').value  = catId ?? '';
  el('catalystModalTitle').textContent = catId ? 'Chỉnh sửa Catalyst' : 'Thêm Catalyst';
  el('catalystForm').reset();
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
      el('catalystTimelineField').value  = c.expected_timeline ?? '';
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
export function wireDetailActions(thesisId, wrap) {
  wrap.querySelector('#detailEditBtn')?.addEventListener('click', () => openEditThesisModal(thesisId));
  wrap.querySelector('#detailDeleteBtn')?.addEventListener('click', () => confirmDeleteThesis(thesisId));
  wrap.querySelector('#addAssumBtn')?.addEventListener('click', () => openAssumptionModal(thesisId, null));
  wrap.querySelectorAll('.edit-assum-btn').forEach(btn =>
    btn.addEventListener('click', () => openAssumptionModal(thesisId, btn.dataset.id)));
  wrap.querySelectorAll('.delete-assum-btn').forEach(btn =>
    btn.addEventListener('click', () => confirmDeleteAssumption(thesisId, btn.dataset.id)));
  wrap.querySelector('#addCatBtn')?.addEventListener('click', () => openCatalystModal(thesisId, null));
  wrap.querySelectorAll('.edit-cat-btn').forEach(btn =>
    btn.addEventListener('click', () => openCatalystModal(thesisId, btn.dataset.id)));
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
// bindThesisFormEvents — đăng ký form submit + global delete confirm
// Gọi 1 lần từ app.js khi DOMContentLoaded
// ---------------------------------------------------------------------------
export function bindThesisFormEvents({ onThesisSaved }) {
  // Thesis form submit
  el('thesisForm')?.addEventListener('submit', async e => {
    e.preventDefault();
    const btn = el('thesisSubmitBtn');
    btn.classList.add('btn-loading');
    btn.textContent = 'Đang lưu…';
    const id   = el('thesisIdField').value;
    const payload = {
      ticker:       el('thesisTickerField').value.trim().toUpperCase(),
      title:        el('thesisTitleField').value.trim(),
      summary:      el('thesisSummaryField').value.trim() || null,
      entry_price:  el('thesisEntryField').value  ? Number(el('thesisEntryField').value)  : null,
      target_price: el('thesisTargetField').value ? Number(el('thesisTargetField').value) : null,
      stop_loss:    el('thesisStopField').value   ? Number(el('thesisStopField').value)   : null,
      status:       el('thesisStatusField').value,
      direction:    el('thesisDirectionField').value,
    };
    const assumptions = collectFormAssumptions();
    const catalysts   = collectFormCatalysts();
    try {
      let thesisId = id;
      if (id) {
        await sendJson(`${thesisApiBase()}/${id}`, 'PATCH', payload);
        await syncNewDetailItems(id, assumptions, catalysts);
        showToast('✅ Đã cập nhật thesis');
        thesisId = id;
      } else {
        const created = await sendJson(`${thesisApiBase()}`, 'POST', payload);
        thesisId = created?.id ?? null;
        state.selectedThesisId = thesisId;
        if (thesisId) {
          for (const a of assumptions)
            await sendJson(`${thesisApiBase()}/${thesisId}/assumptions`, 'POST', { ...a, status: 'pending', confidence: null });
          for (const c of catalysts)
            await sendJson(`${thesisApiBase()}/${thesisId}/catalysts`, 'POST', { ...c, status: 'pending' });
        }
        showToast('✅ Đã tạo thesis mới');
      }
      closeModal('thesisModal');
      await onThesisSaved(thesisId);
    } catch (err) {
      showToast(`Lỗi: ${err.message}`, 'error');
    } finally {
      btn.classList.remove('btn-loading');
      btn.textContent = 'Lưu Thesis';
    }
  });

  // Assumption form submit
  el('assumptionForm')?.addEventListener('submit', async e => {
    e.preventDefault();
    const thesisId = el('assumptionThesisId').value;
    const assumId  = el('assumptionIdField').value;
    const payload = {
      description: el('assumptionDescField').value.trim(),
      rationale:   el('assumptionRationaleField').value.trim() || null,
      status:      el('assumptionStatusField').value,
      confidence:  el('assumptionConfidenceField').value ? Number(el('assumptionConfidenceField').value) : null,
    };
    try {
      if (assumId) {
        await sendJson(`${thesisApiBase()}/${thesisId}/assumptions/${assumId}`, 'PATCH', payload);
        showToast('✅ Đã cập nhật assumption');
      } else {
        await sendJson(`${thesisApiBase()}/${thesisId}/assumptions`, 'POST', payload);
        showToast('✅ Đã thêm assumption');
      }
      closeModal('assumptionModal');
      await loadThesisDetail(thesisId);
    } catch (err) {
      showToast(`Lỗi: ${err.message}`, 'error');
    }
  });

  // Catalyst form submit
  el('catalystForm')?.addEventListener('submit', async e => {
    e.preventDefault();
    const thesisId = el('catalystThesisId').value;
    const catId    = el('catalystIdField').value;
    const payload = {
      description:       el('catalystDescField').value.trim(),
      rationale:         el('catalystRationaleField').value.trim() || null,
      status:            el('catalystStatusField').value,
      expected_timeline: el('catalystTimelineField').value.trim() || null,
    };
    try {
      if (catId) {
        await sendJson(`${thesisApiBase()}/${thesisId}/catalysts/${catId}`, 'PATCH', payload);
        showToast('✅ Đã cập nhật catalyst');
      } else {
        await sendJson(`${thesisApiBase()}/${thesisId}/catalysts`, 'POST', payload);
        showToast('✅ Đã thêm catalyst');
      }
      closeModal('catalystModal');
      await loadThesisDetail(thesisId);
    } catch (err) {
      showToast(`Lỗi: ${err.message}`, 'error');
    }
  });

  // Global delete confirm
  el('deleteConfirmBtn')?.addEventListener('click', async () => {
    if (!state.deleteCallback) return;
    const btn = el('deleteConfirmBtn');
    btn.classList.add('btn-loading');
    btn.textContent = 'Đang xóa…';
    try { await state.deleteCallback(); }
    catch (err) { showToast(`Lỗi xóa: ${err.message}`, 'error'); }
    finally {
      btn.classList.remove('btn-loading');
      btn.textContent = 'Xóa';
      state.deleteCallback = null;
    }
  });
}
