import { esc } from '../../utils/format.js';
import { state } from '../../state/dashboard-state.js';

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
          <h3>Agent Suggestion</h3>
        </div>
        <button
          class="suggest-btn"
          id="aiReviewBtn-${thesisId}"
          style="min-height:30px;padding:0 14px;font-size:.8rem;margin-left:auto;"
        >
          Verify
        </button>
      </div>
      <div id="aiReviewLoading-${thesisId}" class="suggest-loading hidden">
        <div class="spinner"></div>
        AI đang phân tích thesis...
      </div>
      <div id="aiReviewResult-${thesisId}" class="suggest-result hidden"></div>
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
export function renderReviewRecommendResult(thesisId, d) {
  // Guard: nếu response rỗng, trả về error state thay vì crash
  if (!d || typeof d !== 'object') {
    return `<div class="error-banner" style="margin:0;">AI review không trả về kết quả hợp lệ.</div>`;
  }

  console.log('[AI Review raw response]', JSON.stringify(d));
  state.latestAiReviews[thesisId] = d;

  const confPct      = Math.round((d.confidence ?? 0) * 100);
  const verdictCls   = (String(d.verdict ?? '').toLowerCase() || 'neutral') || 'neutral';
  const risks        = d.risk_signals ?? d.risks ?? [];
  const watches      = d.next_watch_items ?? d.nextwatchitems ?? [];
  const riskItems    = risks.map(r => `<li>${esc(r)}</li>`).join('');
  const watchItems   = watches.map(w => `<li>${esc(w)}</li>`).join('');

  // Gợi ý action type dựa trên verdict — dùng để pre-fill nút B/S
  const verdictUpper = String(d.verdict ?? '').toUpperCase();
  const suggestedType = verdictUpper === 'BUY' ? 'BUY'
    : verdictUpper === 'SELL' ? 'SELL'
    : null;

  // Lấy ticker từ thesis đang mở (state.theses đã load)
  const thesis  = state.theses?.find(t => String(t.id) === String(thesisId));
  const ticker  = thesis?.ticker ?? '';

  // Quick-trade buttons — chỉ hiện nếu verdict là BUY hoặc SELL
  const quickTradeHTML = ticker ? `
    <div class="review-quick-trade" style="display:flex;gap:8px;margin-top:12px;align-items:center;flex-wrap:wrap;">
      <span style="font-size:.8rem;color:var(--muted);">📌 Ghi nhanh lệnh cho <strong>${esc(ticker)}</strong>:</span>
      <button
        class="review-trade-btn review-trade-btn--buy ghost-btn"
        data-trade-ticker="${esc(ticker)}"
        data-trade-thesis-id="${thesisId}"
        data-trade-type="BUY"
        style="min-height:28px;padding:0 14px;font-size:.8rem;font-weight:600;
               color:#6daa45;border-color:rgba(109,170,69,.4);
               ${suggestedType === 'BUY' ? 'background:rgba(109,170,69,.1);' : ''}"
        title="Log lệnh MUA ${esc(ticker)}"
      >B</button>
      <button
        class="review-trade-btn review-trade-btn--sell ghost-btn"
        data-trade-ticker="${esc(ticker)}"
        data-trade-thesis-id="${thesisId}"
        data-trade-type="SELL"
        style="min-height:28px;padding:0 14px;font-size:.8rem;font-weight:600;
               color:#dd6974;border-color:rgba(221,105,116,.4);
               ${suggestedType === 'SELL' ? 'background:rgba(221,105,116,.1);' : ''}"
        title="Log lệnh BÁN ${esc(ticker)}"
      >S</button>
    </div>` : '';

  return `
    <div class="suggest-body">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
        <span class="badge ${verdictCls}" style="font-size:.95rem;padding:6px 14px;">
          ${esc(String(d.verdict ?? '').toUpperCase())}
        </span>
        <span style="color:var(--muted);font-size:.85rem;">Confidence ${confPct}%</span>
      </div>

      <div class="confidence-bar" style="margin-bottom:12px;">
        <div class="confidence-fill" style="width:${confPct}%;"></div>
      </div>

      ${d.reasoning ? `<p style="line-height:1.65;margin-bottom:10px;">${esc(d.reasoning)}</p>` : ''}

      ${riskItems ? `
        <div>
          <p class="suggest-section-title">Risk signals</p>
          <ul style="padding-left:1.2em;color:var(--muted);font-size:.88rem;">${riskItems}</ul>
        </div>` : ''}

      ${watchItems ? `
        <div style="margin-top:10px;">
          <p class="suggest-section-title">Next watch items</p>
          <ul style="padding-left:1.2em;color:var(--muted);font-size:.88rem;">${watchItems}</ul>
        </div>` : ''}

      <div style="display:flex;flex-direction:column;gap:6px;margin-top:14px;">
        <div style="font-size:0.8rem;color:var(--muted);">
          <strong>AI check xong — gợi ý của AI:</strong><br/>
          • Verdict: ${esc(String(d.verdict ?? '').toUpperCase()) || 'N/A'}, confidence ${confPct}%<br/>
          ${risks[0] ? `• Rủi ro chính: ${esc(risks[0])}` : '• Rủi ro chính: Chưa có rủi ro nổi bật được nêu rõ.'}
        </div>

        ${quickTradeHTML}

        <div style="display:flex;gap:10px;margin-top:10px;align-items:center;flex-wrap:wrap;">
          <span style="
            display:inline-flex;align-items:center;gap:6px;
            background:rgba(109,170,69,.15);color:#6daa45;
            border:1px solid rgba(109,170,69,.3);
            border-radius:999px;padding:4px 12px;font-size:.82rem;font-weight:600;
          ">✓ Đã áp dụng tự động</span>
          <button
            class="ghost-btn dismiss-ai-review-btn"
            data-thesis-id="${thesisId}"
            style="min-height:30px;padding:0 10px;font-size:.8rem;"
          >Đóng</button>
        </div>
      </div>
    </div>
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
