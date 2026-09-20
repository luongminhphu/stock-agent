import { esc } from '../../utils/format.js?v=1';
import { state } from '../../state/dashboard-state.js?v=1';

/**
 * Render vùng chứa AI Review (nút Verify + loading + result placeholder).
 * @param {string|number} thesisId
 * @returns {string} HTML string
 */
export function renderReviewRecommendSection(thesisId) {
  return `
    <div class="detail-section" id="reviewRecommendSection-${thesisId}">
      <div class="detail-section-header" style="align-items:flex-end; gap:12px;">
        <div style="max-width: 65%;">
          <h3>Phản biện AI</h3>
        </div>
        <button
          class="suggest-btn"
          id="aiReviewBtn-${thesisId}"
          style="min-height:30px;padding:0 14px;font-size:.8rem;margin-left:auto;"
        >
          Kiểm chứng thesis
        </button>
      </div>
      <div id="aiReviewLoading-${thesisId}" class="suggest-loading hidden">
        <div class="spinner"></div>
        AI đang phân tích thesis…
      </div>
      <div id="aiReviewResult-${thesisId}" class="suggest-result suggest-result--card hidden"></div>
    </div>
  `;
}

/**
 * Render kết quả AI review vào vùng result.
 * Side-effect: cache vào state.latestAiReviews.
 * @param {string|number} thesisId
 * @param {object} d  - response payload từ AI review endpoint
 * @returns {string} HTML string
 *
 * FIX: guard d null/undefined — tránh '(destructured parameter) is undefined' từ V8
 */
const VERDICT_VN = {
  BUY: 'Mua', SELL: 'Bán', HOLD: 'Giữ', WATCH: 'Theo dõi',
  REDUCE: 'Giảm', ADD: 'Gia tăng', NEUTRAL: 'Trung lập', INVALIDATE: 'Vô hiệu',
};

/**
 * AiVerdictCard (Wave U2b) — hierarchy cố định cho mọi AI output:
 *   verdict → risk signals → next watch items → confidence → action → reasoning.
 * Payload: ThesisReviewResponse {verdict, confidence, reasoning, risk_signals,
 * next_watch_items, reviewed_at, reviewed_price}. Side-effect: cache vào state.
 * @param {string|number} thesisId
 * @param {object} d
 * @returns {string} HTML
 */
export function renderReviewRecommendResult(thesisId, d) {
  if (!d || typeof d !== 'object') {
    return `<div class="error-banner" style="margin:0;">AI review không trả về kết quả hợp lệ.</div>`;
  }
  state.latestAiReviews[thesisId] = d;

  const verdictUpper = String(d.verdict ?? '').toUpperCase();
  const verdictCls   = ['BUY','ADD'].includes(verdictUpper) ? 'buy'
    : ['SELL','REDUCE','INVALIDATE'].includes(verdictUpper) ? 'sell'
    : verdictUpper === 'HOLD' ? 'hold'
    : verdictUpper === 'WATCH' ? 'watch' : 'neutral';
  const verdictLabel = VERDICT_VN[verdictUpper] ?? verdictUpper ?? '—';
  const confPct      = Math.round((d.confidence ?? 0) * 100);
  const confTone     = confPct >= 70 ? 'high' : confPct >= 45 ? 'mid' : 'low';
  const risks        = Array.isArray(d.risk_signals) ? d.risk_signals : (d.risks ?? []);
  const watches      = Array.isArray(d.next_watch_items) ? d.next_watch_items : [];
  const reviewedAt   = d.reviewed_at ? new Date(d.reviewed_at) : null;
  const reviewedStr  = reviewedAt && !Number.isNaN(reviewedAt.getTime())
    ? reviewedAt.toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
    : '';

  const thesis = state.theses?.find(t => String(t.id) === String(thesisId));
  const ticker = thesis?.ticker ?? '';
  const suggested = verdictUpper === 'BUY' || verdictUpper === 'ADD' ? 'BUY'
    : verdictUpper === 'SELL' || verdictUpper === 'REDUCE' ? 'SELL'
    : null;

  const list = (items, emptyText) => items.length
    ? `<ul class="avc-list">${items.map(x => `<li>${esc(x)}</li>`).join('')}</ul>`
    : `<p class="avc-empty">${emptyText}</p>`;

  // Action — CTA giao dịch là nút duy nhất được viết HOA (quy tắc HSC).
  const actionHTML = ticker ? `
      <div class="avc-actions">
        <button class="review-trade-btn avc-cta avc-cta--buy ${suggested === 'BUY' ? 'is-suggested' : ''}"
          data-trade-ticker="${esc(ticker)}" data-trade-thesis-id="${thesisId}" data-trade-type="BUY"
          title="Ghi lệnh mua ${esc(ticker)}">MUA</button>
        <button class="review-trade-btn avc-cta avc-cta--sell ${suggested === 'SELL' ? 'is-suggested' : ''}"
          data-trade-ticker="${esc(ticker)}" data-trade-thesis-id="${thesisId}" data-trade-type="SELL"
          title="Ghi lệnh bán ${esc(ticker)}">BÁN</button>
        <span class="avc-actions-note">Ghi nhận quyết định cho ${esc(ticker)} vào nhật ký</span>
        <button class="ghost-btn dismiss-ai-review-btn avc-dismiss" data-thesis-id="${thesisId}">Đóng</button>
      </div>` : '';

  return `
    <article class="ai-verdict-card" data-thesis-id="${thesisId}" aria-label="Kết luận AI review">
      <header class="avc-head">
        <span class="avc-verdict avc-verdict--${verdictCls}">${esc(verdictLabel)}</span>
        <span class="avc-meta">${reviewedStr ? `Review lúc ${esc(reviewedStr)}` : 'Review vừa xong'}${d.reviewed_price ? ` \u00b7 giá ${Number(d.reviewed_price).toLocaleString('vi-VN')}` : ''}</span>
      </header>

      <section class="avc-section">
        <p class="suggest-section-title">Tín hiệu rủi ro</p>
        ${list(risks, 'Chưa có rủi ro nổi bật được nêu rõ.')}
      </section>

      <section class="avc-section">
        <p class="suggest-section-title">Theo dõi tiếp</p>
        ${list(watches, 'Không có mục cần theo dõi thêm.')}
      </section>

      <section class="avc-section avc-confidence" aria-label="Độ tin cậy ${confPct}%">
        <p class="suggest-section-title">Độ tin cậy</p>
        <div class="avc-confidence-row">
          <div class="confidence-bar"><div class="confidence-fill avc-fill--${confTone}" style="width:${confPct}%;"></div></div>
          <span class="avc-confidence-value">${confPct}%</span>
        </div>
      </section>

      ${actionHTML}

      ${d.reasoning ? `
      <details class="avc-reasoning">
        <summary>Lý do tóm tắt</summary>
        <p>${esc(d.reasoning)}</p>
      </details>` : ''}
    </article>
  `;
}

/**
 * wireReviewQuickTrade — gắn click handler cho các nút B/S trong AI review result.
 * Gọi sau khi inject renderReviewRecommendResult vào DOM.
 *
 * Khi user nhấn B hoặc S:
 *   1. Dispatch CustomEvent 'openDecisionModal:prefill' với { ticker, thesisId, decisionType }
 *   2. app.js lắng nghe event này và mở modal Log Decision với giá trị pre-fill sẵn.
 *
 * @param {HTMLElement} container — element chứa kết quả review (aiReviewResult-{id})
 */
export function wireReviewQuickTrade(container) {
  if (!container) return;
  container.addEventListener('click', (e) => {
    const btn = e.target.closest('.review-trade-btn');
    if (!btn) return;
    e.stopPropagation();

    const ticker     = btn.dataset.tradeTicker;
    const thesisId   = btn.dataset.tradeThesisId ? Number(btn.dataset.tradeThesisId) : null;
    const decisionType = btn.dataset.tradeType; // 'BUY' | 'SELL'

    if (!ticker || !decisionType) return;

    document.dispatchEvent(new CustomEvent('openDecisionModal:prefill', {
      detail: { ticker, thesisId, decisionType },
    }));
  });
}
