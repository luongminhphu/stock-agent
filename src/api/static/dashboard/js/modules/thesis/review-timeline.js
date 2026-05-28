/**
 * review-timeline.js
 * Owner: modules/thesis
 * Responsibility: Fetch + render focused AI review timeline
 *   (5 reviews gần nhất) từ GET /api/v1/readmodel/thesis/{id}/review-timeline
 *
 * Public API:
 *   reviewTimelineSlotHTML(thesisId)  — skeleton slot HTML, inject vào detail panel
 *   loadReviewTimeline(thesisId)       — fetch + render vào slot
 */

import { esc, fmtDate } from '../../utils/format.js';
import { readmodelApiBase, getJson } from '../../api/client.js';

// ---------------------------------------------------------------------------
// Verdict helpers
// ---------------------------------------------------------------------------

const VERDICT_META = {
  BULLISH:   { cls: 'rv-verdict--bull',  icon: '🟢' },
  BEARISH:   { cls: 'rv-verdict--bear',  icon: '🔴' },
  NEUTRAL:   { cls: 'rv-verdict--neut',  icon: '🟡' },
  WATCHLIST: { cls: 'rv-verdict--watch', icon: '👁' },
};

function verdictTag(verdict) {
  if (!verdict) return '';
  const v = String(verdict).toUpperCase();
  const m = VERDICT_META[v] ?? { cls: 'rv-verdict--neut', icon: '•' };
  return `<span class="rv-verdict ${m.cls}">${m.icon} ${esc(v)}</span>`;
}

// ---------------------------------------------------------------------------
// Single review card
// ---------------------------------------------------------------------------

function reviewCardHTML(item) {
  const conf   = item.confidence_pct != null ? `<span class="rv-chip">Conf ${item.confidence_pct}%</span>` : '';
  const price  = item.reviewed_price
    ? `<span class="rv-chip">${Number(item.reviewed_price).toLocaleString('vi-VN')}₫</span>`
    : '';

  const risks = item.risk_signals?.length
    ? `<div class="rv-signals">
        <span class="rv-signals-label">⚠ Risks</span>
        ${item.risk_signals.map(r => `<span class="rv-signal-tag">${esc(r)}</span>`).join('')}
       </div>`
    : '';

  const watches = item.next_watch_items?.length
    ? `<div class="rv-signals">
        <span class="rv-signals-label">👁 Watch</span>
        ${item.next_watch_items.map(w => `<span class="rv-signal-tag rv-signal-tag--watch">${esc(w)}</span>`).join('')}
       </div>`
    : '';

  const reasoning = item.reasoning
    ? `<p class="rv-reasoning">${esc(item.reasoning)}</p>`
    : '';

  return `
    <div class="rv-card">
      <div class="rv-card-header">
        <div class="rv-card-meta">
          ${verdictTag(item.verdict)}
          ${conf}
          ${price}
        </div>
        <span class="rv-card-date">${fmtDate(item.reviewed_at)}</span>
      </div>
      ${reasoning}
      ${risks}
      ${watches}
    </div>`;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export function reviewTimelineSlotHTML(thesisId) {
  return `
    <div id="reviewTimelineSlot-${thesisId}" class="rv-slot" aria-live="polite">
      <div class="rv-section">
        <div class="rv-section-title">🔍 AI Reviews gần nhất</div>
        <div class="rv-skeleton">
          <div class="skel skel-text" style="width:60%;"></div>
          <div class="skel skel-text" style="width:45%;"></div>
          <div class="skel skel-text" style="width:70%;"></div>
        </div>
      </div>
    </div>`;
}

export async function loadReviewTimeline(thesisId) {
  const slot = document.getElementById(`reviewTimelineSlot-${thesisId}`);
  if (!slot) return;

  try {
    const data = await getJson(`${readmodelApiBase()}/thesis/${thesisId}/review-timeline`);

    if (!data || !Array.isArray(data.items) || !data.items.length) {
      slot.innerHTML = `
        <div class="rv-section">
          <div class="rv-section-title">🔍 AI Reviews gần nhất</div>
          <p class="empty-state">Chưa có AI review nào. Trigger review để bắt đầu.</p>
        </div>`;
      return;
    }

    slot.innerHTML = `
      <div class="rv-section">
        <div class="rv-section-title">
          🔍 AI Reviews gần nhất
          <span class="rv-count-badge">${data.items.length}${
            data.total > data.items.length ? `/${data.total}` : ''
          }</span>
        </div>
        <div class="rv-list">
          ${data.items.map(reviewCardHTML).join('')}
        </div>
      </div>`;

  } catch (err) {
    slot.innerHTML = `
      <div class="rv-section">
        <div class="rv-section-title">🔍 AI Reviews gần nhất</div>
        <p class="error-text">Lỗi load reviews: ${esc(err.message)}</p>
      </div>`;
  }
}
