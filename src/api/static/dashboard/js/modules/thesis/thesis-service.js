import { el, showToast, openModal, closeModal } from '../../utils/dom.js';
import { esc } from '../../utils/format.js';
import { thesisApiBase, apiBase, getJson, sendJson } from '../../api/client.js';
import { state, resetAiApply } from '../../state/dashboard-state.js';
import {
  renderThesisDetailHTML,
  renderAssumItem,
  renderCatItem,
  emptyDetailHTML,
} from './render-thesis-table.js';
import { wireDetailActions } from './wire-detail-actions.js';

export async function loadThesisDetail(thesisId) {
  const wrap = el('thesisDetail');
  wrap.innerHTML = '<div class="empty-detail"><div class="spinner"></div></div>';
  try {
    const [thesis, assumptions, catalysts, reviews] = await Promise.all([
      getJson(`${thesisApiBase()}/${thesisId}`),
      getJson(`${thesisApiBase()}/${thesisId}/assumptions`).catch(() => []),
      getJson(`${thesisApiBase()}/${thesisId}/catalysts`).catch(() => []),
      getJson(`${thesisApiBase()}/${thesisId}/reviews`).catch(() => []),
    ]);
    wrap.innerHTML = renderThesisDetailHTML(thesis, assumptions, catalysts, reviews);
    wireDetailActions(thesisId, wrap);
  } catch (err) {
    wrap.innerHTML = `<div class="error-banner">Lỗi tải chi tiết: ${err.message}</div>${emptyDetailHTML()}`;
  }
}

export async function triggerAiReview(thesisId) {
  const loading = el(`aiReviewLoading-${thesisId}`);
  const result  = el(`aiReviewResult-${thesisId}`);
  const btn     = el(`aiReviewBtn-${thesisId}`);
  if (!loading || !result) return;
  btn && (btn.disabled = true);
  loading.classList.remove('hidden');
  result.classList.add('hidden');
  result.innerHTML = '';
  try {
    const data = await sendJson(`${thesisApiBase()}/${thesisId}/review`, 'POST', null);
    const { renderReviewRecommendResult } = await import('./render-ai-review.js');
    result.innerHTML = renderReviewRecommendResult(thesisId, data);
    result.classList.remove('hidden');
  } catch (err) {
    result.innerHTML = `<div class="error-banner" style="margin:0;">AI review lỗi: ${err.message}</div>`;
    result.classList.remove('hidden');
  } finally {
    loading.classList.add('hidden');
    btn && (btn.disabled = false);
  }
}

export async function openApplyAiReviewModal(thesisId) {
  state.aiApplyThesisId = thesisId;
  state.aiSelectedRecIds = [];
  const body       = el('aiApplyModalBody');
  const confirmBtn = el('aiApplyConfirmBtn');
  if (!body) return;
  body.innerHTML = '<p class="empty-state">Đang tải gợi ý từ AI...</p>';
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Đang tải...'; }
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
    if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = 'Áp dụng'; }
    body.querySelectorAll('.ai-rec-checkbox').forEach(chk => {
      chk.addEventListener('change', () => {
        state.aiSelectedRecIds = Array.from(body.querySelectorAll('.ai-rec-checkbox:checked'))
          .map(c => c.dataset.recId);
      });
    });
  } catch (err) {
    body.innerHTML = `<div class="error-banner">Lỗi tải recommendations: ${err.message}</div>`;
  }
}
