/**
 * thesis-service.js
 * Owner: modules/thesis
 * Responsibility: API calls + side effects liên quan đến thesis lifecycle.
 * - loadThesisDetail()
 * - triggerAiReview()
 * - openApplyAiReviewModal()
 * - confirmDeleteThesis / Assumption / Catalyst
 */

import { el, showToast, openModal, closeModal } from '../../utils/dom.js';
import { esc } from '../../utils/format.js';
import { thesisApiBase, getJson, sendJson } from '../../api/client.js';
import { state } from '../../state/dashboard-state.js';
import { renderThesisDetailHTML, emptyDetailHTML } from './render-thesis-table.js';
import { wireDetailActions } from './thesis-form.js';
import { renderReviewRecommendResult } from './render-ai-review.js';

// ---------------------------------------------------------------------------
// Skeleton HTML cho detail-shell
// Mirrors layout: heading → badges row → 3-col stats → 2-col columns
// ---------------------------------------------------------------------------
function detailSkeletonHTML() {
  return `
    <div class="skel-detail-wrap" aria-busy="true" aria-label="Đang tải chi tiết thesis…">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">
        <div style="flex:1;">
          <div style="display:flex;gap:6px;margin-bottom:8px;">
            <div class="skel skel-badge"></div>
            <div class="skel skel-badge"></div>
            <div class="skel skel-badge"></div>
          </div>
          <div class="skel skel-heading"></div>
          <div class="skel skel-text" style="width:80%;"></div>
        </div>
        <div style="display:flex;gap:8px;flex-shrink:0;">
          <div class="skel skel-badge" style="width:72px;"></div>
          <div class="skel skel-badge" style="width:80px;"></div>
        </div>
      </div>
      <div class="skel-stat-grid">
        <div class="skel skel-stat"></div>
        <div class="skel skel-stat"></div>
        <div class="skel skel-stat"></div>
        <div class="skel skel-stat"></div>
        <div class="skel skel-stat"></div>
        <div class="skel skel-stat"></div>
      </div>
      <div class="skel-columns">
        <div>
          <div class="skel skel-text" style="width:50%;margin-bottom:10px;"></div>
          <div class="skel skel-item"></div>
          <div class="skel skel-item"></div>
        </div>
        <div>
          <div class="skel skel-text" style="width:50%;margin-bottom:10px;"></div>
          <div class="skel skel-item"></div>
          <div class="skel skel-item"></div>
        </div>
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// Load detail panel
// ---------------------------------------------------------------------------
export async function loadThesisDetail(thesisId) {
  const wrap = el('thesisDetail');
  // WAVE 2d: real skeleton thay vì spinner trơ
  wrap.classList.remove('empty-detail');
  wrap.innerHTML = detailSkeletonHTML();
  try {
    const [thesis, assumptions, catalysts, reviews] = await Promise.all([
      getJson(`${thesisApiBase()}/${thesisId}`),
      getJson(`${thesisApiBase()}/${thesisId}/assumptions`).catch(() => []),
      getJson(`${thesisApiBase()}/${thesisId}/catalysts`).catch(() => []),
      getJson(`${thesisApiBase()}/${thesisId}/reviews`).catch(() => []),
    ]);
    // FIX: guard thesis null/undefined — có thể xảy ra khi API trả 204 hoặc null
    if (!thesis) {
      wrap.innerHTML = emptyDetailHTML();
      return;
    }
    wrap.innerHTML = renderThesisDetailHTML(thesis, assumptions, catalysts, reviews);
    wireDetailActions(thesisId, wrap);
  } catch (err) {
    wrap.innerHTML = `<div class="error-banner">Lỗi tải chi tiết: ${err.message}</div>${emptyDetailHTML()}`;
  }
}

// ---------------------------------------------------------------------------
// AI Review
// ---------------------------------------------------------------------------
export async function triggerAiReview(thesisId) {
  const btn     = el(`aiReviewBtn-${thesisId}`);
  const loading = el(`aiReviewLoading-${thesisId}`);
  const result  = el(`aiReviewResult-${thesisId}`);
  if (!loading || !result) return;

  if (btn) btn.disabled = true;
  loading.classList.remove('hidden');
  result.classList.add('hidden');

  try {
    const data = await sendJson(`${thesisApiBase()}/${thesisId}/review`, 'POST', null);
    // FIX: guard data null/undefined — AI endpoint đôi khi trả về null khi lỗi upstream
    if (!data) {
      result.innerHTML = `<div class="error-banner" style="margin:0;">AI review không trả về kết quả.</div>`;
      result.classList.remove('hidden');
      return;
    }
    result.innerHTML = renderReviewRecommendResult(thesisId, data);
    result.classList.remove('hidden');
  } catch (err) {
    result.innerHTML = `<div class="error-banner" style="margin:0;">AI review lỗi: ${esc(err.message)}</div>`;
    result.classList.remove('hidden');
  } finally {
    loading.classList.add('hidden');
    if (btn) btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Apply AI Review Modal
// ---------------------------------------------------------------------------
export async function openApplyAiReviewModal(thesisId) {
  state.aiApplyThesisId = thesisId;
  state.aiSelectedRecIds = [];

  const body       = el('aiApplyModalBody');
  const confirmBtn = el('aiApplyConfirmBtn');
  if (!body) return;

  body.innerHTML = '<p class="empty-state">Đang tải gợi ý từ AI...</p>';
  if (confirmBtn) {
    confirmBtn.disabled = true;
    confirmBtn.textContent = 'Đang tải...';
  }

  openModal('aiApplyModal');

  try {
    const res   = await getJson(`${thesisApiBase()}/${thesisId}/recommendations`);
    const items = Array.isArray(res) ? res : (res?.items ?? []);

    if (!items.length) {
      body.innerHTML = '<p class="empty-state">Không còn gợi ý nào đang chờ áp dụng.</p>';
      return;
    }

    state.aiSelectedRecIds = items.map(r => r.id);

    body.innerHTML = `
      <div class="review-columns">
        <div class="review-box">
          <p class="suggest-section-title">Assumptions</p>
          ${items.filter(r => r.target_type === 'assumption').map(r => `
            <label class="suggest-item">
              <div style="display:flex;align-items:flex-start;gap:8px">
                <input type="checkbox" class="ai-rec-checkbox" data-rec-id="${r.id}" checked>
                <div>
                  <strong>${esc(r.target_description ?? '')}</strong>
                  <span> → <b>${esc(r.recommended_status ?? '')}</b>: ${esc(r.reason ?? '')}</span>
                </div>
              </div>
            </label>`).join('') || '<p class="empty-state">Không có assumption nào.</p>'}
        </div>
        <div class="review-box">
          <p class="suggest-section-title">Catalysts</p>
          ${items.filter(r => r.target_type === 'catalyst').map(r => `
            <label class="suggest-item">
              <div style="display:flex;align-items:flex-start;gap:8px">
                <input type="checkbox" class="ai-rec-checkbox" data-rec-id="${r.id}" checked>
                <div>
                  <strong>${esc(r.target_description ?? '')}</strong>
                  <span> → <b>${esc(r.recommended_status ?? '')}</b>: ${esc(r.reason ?? '')}</span>
                </div>
              </div>
            </label>`).join('') || '<p class="empty-state">Không có catalyst nào.</p>'}
        </div>
      </div>`;

    body.querySelectorAll('.ai-rec-checkbox').forEach(cb => {
      cb.addEventListener('change', () => {
        const id = Number(cb.dataset.recId);
        if (cb.checked) {
          if (!state.aiSelectedRecIds.includes(id)) state.aiSelectedRecIds.push(id);
        } else {
          state.aiSelectedRecIds = state.aiSelectedRecIds.filter(x => x !== id);
        }
      });
    });

  } catch (err) {
    body.innerHTML = `<div class="error-banner" style="margin:0">Không tải được gợi ý: ${esc(err.message)}</div>`;
  } finally {
    if (confirmBtn) {
      confirmBtn.disabled = false;
      confirmBtn.textContent = 'Xác nhận áp dụng';
    }
  }
}

// ---------------------------------------------------------------------------
// Delete confirmations
// ---------------------------------------------------------------------------
export function confirmDeleteThesis(thesisId) {
  const t = state.theses.find(x => x.id === thesisId);
  el('deleteModalMsg').textContent =
    `Bạn chắc chắn muốn xóa thesis "${t?.title ?? thesisId}" (${t?.ticker ?? ''})? Thao tác này không thể hoàn tác.`;
  state.deleteCallback = async () => {
    await sendJson(`${thesisApiBase()}/${thesisId}`, 'DELETE');
    state.selectedThesisId = null;
    closeModal('deleteModal');
    showToast('🗑 Đã xóa thesis');
    // loadDashboard được gọi từ app.js hoặc event bus sau khi deleteCallback resolve
  };
  openModal('deleteModal');
}

export function confirmDeleteAssumption(thesisId, assumId) {
  el('deleteModalMsg').textContent = 'Bạn chắc chắn muốn xóa assumption này?';
  state.deleteCallback = async () => {
    await sendJson(`${thesisApiBase()}/${thesisId}/assumptions/${assumId}`, 'DELETE');
    closeModal('deleteModal');
    showToast('🗑 Đã xóa assumption');
    await loadThesisDetail(thesisId);
  };
  openModal('deleteModal');
}

export function confirmDeleteCatalyst(thesisId, catId) {
  el('deleteModalMsg').textContent = 'Bạn chắc chắn muốn xóa catalyst này?';
  state.deleteCallback = async () => {
    await sendJson(`${thesisApiBase()}/${thesisId}/catalysts/${catId}`, 'DELETE');
    closeModal('deleteModal');
    showToast('🗑 Đã xóa catalyst');
    await loadThesisDetail(thesisId);
  };
  openModal('deleteModal');
}
