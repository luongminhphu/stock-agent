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
      <label>Ngày kỳ vọng</label>
      <input type="date" class="form-catalyst-date" value="${data.expected_date ? data.expected_date.slice(0,10) : ''}" />
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

export async function openAssumptionModal(thesisId, assumId) {
  const ticker = state.theses.find(t => String(t.id) === String(thesisId))?.ticker ?? '';
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

export async function openCatalystModal(thesisId, catId) {
  const ticker = state.theses.find(t => String(t.id) === String(thesisId))?.ticker ?? '';
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
export function wireDetailActions(thesisId, wrap) {
  wrap.querySelector('#detailEditBtn')?.addEventListener('click', () => openEditThesisModal(thesisId));
  wrap.querySelector('#detailDeleteBtn')?.addEventListener('click', () => confirmDeleteThesis(thesisId));
  wrap.querySelector('#detailCloseBtn')?.addEventListener('click', () => _confirmLifecycle(thesisId, 'close'));
  wrap.querySelector('#detailInvalidateBtn')?.addEventListener('click', () => _confirmLifecycle(thesisId, 'invalidate'));
  wrap.querySelector('#detailDebateBtn')?.addEventListener('click', () => openDebateModal(thesisId));
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
// Thesis lifecycle: Close + Invalidate
// ---------------------------------------------------------------------------

/**
 * _confirmLifecycle — hiện confirm dialog trước khi POST /close hoặc /invalidate.
 * @param {number} thesisId
 * @param {'close'|'invalidate'} action
 */
async function _confirmLifecycle(thesisId, action) {
  const label    = action === 'close' ? 'đóng' : 'invalidate';
  const icon     = action === 'close' ? '✅' : '⚠️';
  const msgEl    = el('deleteModalMsg');
  const t        = state.theses?.find(x => x.id === thesisId);
  if (msgEl) {
    msgEl.textContent = `${icon} Bạn chắc chắn muốn ${label} thesis "${t?.title ?? thesisId}" (${t?.ticker ?? ''})?`;
  }
  state.deleteCallback = async () => {
    try {
      await sendJson(`${thesisApiBase()}/${thesisId}/${action}`, 'POST', null);
      closeModal('deleteModal');
      showToast(`${icon} Thesis đã được ${label}`);
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
// Debate modal
// ---------------------------------------------------------------------------

/**
 * openDebateModal — mở panel AI Debate cho thesis.
 * POST /thesis/{id}/debate với debate_focus tuỳ chọn.
 */
export async function openDebateModal(thesisId) {
  const t = state.theses?.find(x => x.id === thesisId);

  // Build or reuse modal
  let modal = document.getElementById('debateModal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id        = 'debateModal';
    modal.className = 'modal-overlay hidden';
    modal.innerHTML = `
      <div class="modal-box modal-box--wide">
        <div class="modal-header">
          <h2 class="modal-title">🤺 AI Debate — Devil's Advocate</h2>
          <button class="modal-close" data-close="debateModal" aria-label="Đóng">&#x2715;</button>
        </div>
        <div class="modal-body">
          <div id="debateTickerLine" style="font-size:.88rem;color:var(--muted);margin-bottom:12px;"></div>
          <div class="debate-focus-row" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;">
            <span style="font-size:.82rem;color:var(--muted);align-self:center;">Góc phân tích:</span>
            <button class="ghost-btn ghost-btn--sm debate-focus-btn active" data-focus="">Toàn diện</button>
            <button class="ghost-btn ghost-btn--sm debate-focus-btn" data-focus="entry">Entry</button>
            <button class="ghost-btn ghost-btn--sm debate-focus-btn" data-focus="exit">Exit</button>
            <button class="ghost-btn ghost-btn--sm debate-focus-btn" data-focus="sizing">Sizing</button>
          </div>
          <button class="primary-btn" id="debateRunBtn" style="width:100%;margin-bottom:16px;">🤺 Chạy Debate</button>
          <div id="debateResult" style="min-height:60px;"></div>
        </div>
      </div>`;
    document.body.appendChild(modal);

    // Wire close
    modal.querySelector('[data-close="debateModal"]')?.addEventListener('click', () => closeModal('debateModal'));
    modal.addEventListener('click', e => { if (e.target === modal) closeModal('debateModal'); });

    // Wire focus toggle
    modal.querySelectorAll('.debate-focus-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        modal.querySelectorAll('.debate-focus-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
      });
    });
  }

  // Reset state
  modal.querySelector('#debateTickerLine').textContent =
    t ? `${t.ticker} — ${t.title ?? ''}` : `Thesis #${thesisId}`;
  const resultEl = modal.querySelector('#debateResult');
  resultEl.innerHTML = '';
  modal.querySelectorAll('.debate-focus-btn').forEach(b => b.classList.remove('active'));
  modal.querySelector('.debate-focus-btn[data-focus=""]')?.classList.add('active');

  // Wire run button (re-wire each open to avoid stale thesisId)
  const runBtn = modal.querySelector('#debateRunBtn');
  const newRunBtn = runBtn.cloneNode(true);
  runBtn.replaceWith(newRunBtn);
  newRunBtn.addEventListener('click', async () => {
    const focusBtn = modal.querySelector('.debate-focus-btn.active');
    const focus    = focusBtn?.dataset.focus || null;
    newRunBtn.disabled  = true;
    newRunBtn.textContent = '⏳ Đang phân tích…';
    resultEl.innerHTML  = '<p class="muted" style="padding:8px">AI đang phản biện thesis…</p>';
    try {
      const data = await sendJson(
        `${thesisApiBase()}/${thesisId}/debate`,
        'POST',
        { debate_focus: focus || null },
      );
      resultEl.innerHTML = _renderDebateOutput(data);
    } catch (err) {
      resultEl.innerHTML = `<div class="error-banner" style="margin:0">Lỗi: ${esc(err.message)}</div>`;
    } finally {
      newRunBtn.disabled  = false;
      newRunBtn.textContent = '🤺 Chạy lại';
    }
  });

  openModal('debateModal');
}

/** Render DebateOutput into readable HTML. */
function _renderDebateOutput(d) {
  const stanceIcon = { bull: '🟢', bear: '🔴', neutral: '🟡' }[d.overall_stance] ?? '⚪';
  const strengthClass = { critical: 'badge-danger', significant: 'badge-warn', moderate: 'badge-info', minor: 'badge-muted' };
  const challenges = (d.challenges ?? []).map(c => `
    <div class="debate-challenge" style="margin-bottom:12px;padding:10px 12px;background:var(--surface-2,#1a1a2e);border-radius:6px;border-left:3px solid var(--border);">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
        <span class="badge ${strengthClass[c.strength] ?? 'badge-muted'}" style="font-size:.72rem;">${esc(c.strength?.toUpperCase())}</span>
        <strong style="font-size:.88rem;">${esc(c.area)}</strong>
      </div>
      <p style="font-size:.84rem;margin:0 0 6px;">${esc(c.challenge)}</p>
      ${c.counter_argument ? `<p style="font-size:.80rem;color:var(--muted);margin:0;">💡 ${esc(c.counter_argument)}</p>` : ''}
    </div>`).join('');
  return `
    <div style="margin-bottom:12px;padding:10px 12px;background:var(--surface-2,#1a1a2e);border-radius:6px;">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
        <span>${stanceIcon} <strong>${esc(d.overall_stance?.toUpperCase())}</strong></span>
        <span class="muted" style="font-size:.8rem;">Confidence ${d.confidence?.toFixed(0)}%</span>
      </div>
      <p style="font-size:.88rem;margin:0;">${esc(d.verdict)}</p>
      ${d.suggested_action ? `<p style="font-size:.82rem;color:var(--accent,#7c9ef7);margin:6px 0 0;">→ ${esc(d.suggested_action)}</p>` : ''}
    </div>
    <div class="debate-challenges-list">${challenges}</div>`;
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
      status:       el('thesisStatusField').value,
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
      showToast('✅ Đã lưu catalyst');
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
      showToast('✅ Đã lưu assumption');
      await loadThesisDetail(Number(thesisId));
    } catch (err) {
      showToast(`Lưu assumption thất bại: ${err.message}`, 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Lưu Assumption'; }
    }
  });
}
