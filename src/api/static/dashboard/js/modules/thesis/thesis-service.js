/**
 * thesis-service.js
 * Owner: modules/thesis
 * Responsibility: API calls + side effects liên quan đến thesis lifecycle.
 * - loadThesisDetail()           ← Wave C: inject thesis event timeline
 * - triggerAiReview()
 * - openApplyAiReviewModal()
 * - confirmDeleteThesis / Assumption / Catalyst
 * - bindLessonPersistedEvent()   ← Wave D: listen decision:lesson-persisted
 *
 * Events dispatched:
 *   thesis:review-complete  — after AI review succeeds → AttentionPanel refresh
 */

import { el, showToast, openModal, closeModal } from '../../utils/dom.js';
import { esc, fmtDate } from '../../utils/format.js';
import { thesisApiBase, getJson, sendJson } from '../../api/client.js';
import { state } from '../../state/dashboard-state.js';
import { renderThesisDetailHTML, emptyDetailHTML, wireTabNav } from './render-thesis-table.js';
import { wireDetailActions } from './thesis-form.js';
import { renderReviewRecommendResult, wireReviewQuickTrade } from './render-ai-review.js';
import { fetchQuote, renderQuoteStrip } from './market-quote.js';
import { loadConvictionTimeline } from './conviction-timeline/index.js';
import { loadPriceMiniChart, destroyPriceChart } from './render-price-chart.js';

// ---------------------------------------------------------------------------
// Skeleton HTML
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
// Wave C: render thesis event timeline từ /readmodel/thesis/{id}/timeline
// ---------------------------------------------------------------------------
const TIMELINE_EVENT_META = {
  created:              { icon: '🚀', label: 'Tạo thesis'           },
  assumption_changed:   { icon: '🔄', label: 'Assumption thay đổi'  },
  catalyst_triggered:   { icon: '⚡', label: 'Catalyst kích hoạt'   },
  review_added:         { icon: '📋', label: 'AI Review'             },
  verdict_changed:      { icon: '🎯', label: 'Verdict đổi'          },
  score_updated:        { icon: '📊', label: 'Score cập nhật'       },
  status_changed:       { icon: '🏷',  label: 'Trạng thái đổi'      },
  decision_logged:      { icon: '📝', label: 'Decision ghi lại'     },
  invalidated:          { icon: '❌', label: 'Thesis bị invalidate'  },
};

const TIMELINE_MAX = 30;

function formatEventDetail(raw) {
  if (raw == null) return null;
  if (typeof raw === 'string') {
    const trimmed = raw.trim();
    return trimmed.length ? trimmed : null;
  }
  if (typeof raw === 'number' || typeof raw === 'boolean') return String(raw);
  try {
    const pairs = Object.entries(raw)
      .filter(([, v]) => v != null && v !== '')
      .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${v}`);
    return pairs.length ? pairs.join(' · ') : null;
  } catch {
    return JSON.stringify(raw);
  }
}

function isEventVisible(ev) {
  if (!ev || !ev.event_type) return false;
  const detail = formatEventDetail(ev.detail ?? ev.description ?? ev.summary ?? null);
  if (ev.event_type === 'created') return true;
  return detail != null;
}

function renderThesisTimeline(slot, rawEvents) {
  if (!rawEvents?.length) {
    slot.innerHTML = '<p class="tl-empty">Chưa có sự kiện nào.</p>';
    return;
  }

  const visible = rawEvents.filter(isEventVisible);

  if (!visible.length) {
    slot.innerHTML = '<p class="tl-empty">Chưa có sự kiện nào có nội dung.</p>';
    return;
  }

  const sorted = [...visible].sort((a, b) => {
    const ta = a.occurred_at ? new Date(a.occurred_at).getTime() : 0;
    const tb = b.occurred_at ? new Date(b.occurred_at).getTime() : 0;
    return tb - ta;
  });

  const totalVisible = sorted.length;
  const events = sorted.slice(0, TIMELINE_MAX);
  const truncated = totalVisible - events.length;

  slot.innerHTML = `
    <div class="tl-section">
      <div class="tl-section-title">
        📅 Lịch sử thesis
        <span class="tl-count-badge">${events.length}${truncated > 0 ? `/${totalVisible}` : ''} sự kiện</span>
      </div>
      <ol class="tl-list">
        ${events.map((ev, idx) => {
          const meta    = TIMELINE_EVENT_META[ev.event_type] ?? { icon: '•', label: ev.event_type };
          const dateStr = ev.occurred_at ? fmtDate(ev.occurred_at) : '';
          const detail  = formatEventDetail(ev.detail ?? ev.description ?? ev.summary ?? null);
          const isFirst = idx === 0;
          return `
            <li class="tl-item${isFirst ? ' tl-item--latest' : ''}">
              <span class="tl-icon" aria-hidden="true">${meta.icon}</span>
              <div class="tl-content">
                <div class="tl-label">${esc(meta.label)}${isFirst ? ' <span class="tl-latest-tag">mới nhất</span>' : ''}</div>
                ${detail ? `<div class="tl-detail">${esc(detail)}</div>` : ''}
                ${dateStr ? `<div class="tl-date">${dateStr}</div>` : ''}
              </div>
            </li>`;
        }).join('')}
      </ol>
      ${truncated > 0 ? `<p class="tl-truncated-note">Đang hiển thị 30 sự kiện gần nhất. Còn ${truncated} sự kiện cũ hơn không hiển thị.</p>` : ''}
    </div>`;
}

async function loadThesisTimeline(thesisId, detailWrap) {
  const slot = detailWrap.querySelector(`#thesisTimelineSlot-${thesisId}`);
  if (!slot) return;
  try {
    const res    = await getJson(`/api/v1/readmodel/thesis/${thesisId}/timeline`);
    if (!detailWrap.contains(slot)) return;
    const events = Array.isArray(res) ? res : (res?.events ?? res?.items ?? []);
    renderThesisTimeline(slot, events);
  } catch {
    if (detailWrap.contains(slot)) {
      slot.innerHTML = '<p class="tl-empty muted">Không tải được lịch sử.</p>';
    }
  }
}

// ---------------------------------------------------------------------------
// Load detail panel
// ---------------------------------------------------------------------------
export async function loadThesisDetail(thesisId) {
  const wrap = el('thesisDetail');
  wrap.classList.remove('empty-detail');
  wrap.innerHTML = detailSkeletonHTML();

  destroyPriceChart(thesisId);

  try {
    const [thesis, assumptions, catalysts, reviews] = await Promise.all([
      getJson(`${thesisApiBase()}/${thesisId}`),
      getJson(`${thesisApiBase()}/${thesisId}/assumptions`).catch(() => []),
      getJson(`${thesisApiBase()}/${thesisId}/catalysts`).catch(() => []),
      getJson(`${thesisApiBase()}/${thesisId}/reviews`).catch(() => []),
    ]);
    if (!thesis) {
      wrap.innerHTML = emptyDetailHTML();
      return;
    }

    wrap.innerHTML = renderThesisDetailHTML(thesis, assumptions, catalysts, reviews);

    // Wire tab switching — phải gọi SAU khi set innerHTML
    wireTabNav(wrap);

    wireDetailActions(thesisId, wrap);

    const scheduleIdle = window.requestIdleCallback
      ? (fn) => requestIdleCallback(fn, { timeout: 3000 })
      : (fn) => setTimeout(fn, 0);

    scheduleIdle(async () => {
      const slot = wrap.querySelector('#quoteStripSlot');
      if (!slot) return;
      const quote = await fetchQuote(thesis.ticker);
      if (slot.dataset.ticker !== thesis.ticker) return;
      slot.innerHTML = renderQuoteStrip(quote, thesis);
    });

    scheduleIdle(async () => {
      const slot = wrap.querySelector(`#priceMiniChartSlot-${thesisId}`);
      if (!slot) return;
      await loadPriceMiniChart(thesis, slot);
    });

    scheduleIdle(async () => {
      const slot = wrap.querySelector(`#convictionTimelineSlot-${thesisId}`);
      if (!slot) return;
      await loadConvictionTimeline(thesisId);
    });

    scheduleIdle(async () => {
      await loadThesisTimeline(thesisId, wrap);
    });

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
    if (!data) {
      result.innerHTML = `<div class="error-banner" style="margin:0;">AI review không trả về kết quả.</div>`;
      result.classList.remove('hidden');
      return;
    }
    result.innerHTML = renderReviewRecommendResult(thesisId, data);
    result.classList.remove('hidden');

    // Wire B/S quick-trade buttons sau khi inject HTML vào DOM
    wireReviewQuickTrade(result);

    await loadConvictionTimeline(thesisId);

    // Wave 2 wire: AI review xong → notify app → AttentionPanel refresh
    document.dispatchEvent(new CustomEvent('thesis:review-complete', {
      detail: { thesisId },
    }));
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

// ---------------------------------------------------------------------------
// Wave D: close decision → thesis review UI loop
// ---------------------------------------------------------------------------
export function bindLessonPersistedEvent() {
  document.addEventListener('decision:lesson-persisted', (e) => {
    const { ticker, thesis_id: thesisIdStr } = e.detail ?? {};
    if (!thesisIdStr) return;

    const thesisId = Number(thesisIdStr);

    const row = document.querySelector(`[data-thesis-id="${thesisId}"]`);
    if (row && !row.querySelector('.thesis-lesson-badge')) {
      const badge = document.createElement('span');
      badge.className = 'thesis-lesson-badge';
      badge.title = 'Có AI lesson mới từ Decision Replay — cân nhắc review thesis';
      badge.textContent = '🧠';
      const firstCell = row.querySelector('td');
      if (firstCell) firstCell.appendChild(badge);
    }

    showToast(`🧠 AI lesson mới cho ${ticker ?? 'thesis'} — xem lại thesis để cập nhật assumptions.`);

    if (state.selectedThesisId === thesisId) {
      loadThesisDetail(thesisId);
    }
  });
}
