import { el } from '../../utils/dom.js';
import { triggerAiReview, openApplyAiReviewModal } from './thesis-service.js';
// NOTE: openAssumptionModal, openCatalystModal, confirmDeleteThesis, confirmDeleteAssumption,
// confirmDeleteCatalyst được import từ thesis-service hoặc form modules khi cần;
// file này chỉ chịu trách nhiệm wiring event, không chứa business logic.

/**
 * Wire tất cả event listeners bên trong detail panel sau khi render xong.
 * @param {string|number} thesisId
 * @param {HTMLElement} wrap - container của detail panel
 * @param {object} handlers - { onEdit, onDelete, onAddAssum, onEditAssum, onDeleteAssum, onAddCat, onEditCat, onDeleteCat }
 */
export function wireDetailActions(thesisId, wrap, handlers = {}) {
  wrap.querySelector('#detailEditBtn')?.addEventListener('click', () => handlers.onEdit?.(thesisId));
  wrap.querySelector('#detailDeleteBtn')?.addEventListener('click', () => handlers.onDelete?.(thesisId));

  wrap.querySelector('#addAssumBtn')?.addEventListener('click', () => handlers.onAddAssum?.(thesisId, null));
  wrap.querySelectorAll('.edit-assum-btn').forEach(btn =>
    btn.addEventListener('click', () => handlers.onEditAssum?.(thesisId, btn.dataset.id))
  );
  wrap.querySelectorAll('.delete-assum-btn').forEach(btn =>
    btn.addEventListener('click', () => handlers.onDeleteAssum?.(thesisId, btn.dataset.id))
  );

  wrap.querySelector('#addCatBtn')?.addEventListener('click', () => handlers.onAddCat?.(thesisId, null));
  wrap.querySelectorAll('.edit-cat-btn').forEach(btn =>
    btn.addEventListener('click', () => handlers.onEditCat?.(thesisId, btn.dataset.id))
  );
  wrap.querySelectorAll('.delete-cat-btn').forEach(btn =>
    btn.addEventListener('click', () => handlers.onDeleteCat?.(thesisId, btn.dataset.id))
  );

  wrap.querySelector(`#aiReviewBtn-${thesisId}`)?.addEventListener('click', () => triggerAiReview(thesisId));

  wrap.addEventListener('click', async e => {
    if (e.target.closest('.apply-ai-review-btn')) {
      const btn = e.target.closest('.apply-ai-review-btn');
      openApplyAiReviewModal(Number(btn.dataset.thesisId));
      return;
    }
    if (e.target.closest('.dismiss-ai-review-btn')) {
      const tid = e.target.closest('.dismiss-ai-review-btn').dataset.thesisId;
      const r = wrap.querySelector(`#aiReviewResult-${tid}`);
      if (r) { r.classList.add('hidden'); r.innerHTML = ''; }
    }
  });
}
