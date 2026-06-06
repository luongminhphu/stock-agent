/**
 * intelligence-panel.js
 * Owner: modules/intelligence (readmodel concern)
 *
 * Responsibility: fetch GET /api/v1/readmodel/dashboard/intelligence → render
 * snapshot của IntelligenceEngine ra #intelligencePanel.
 *
 * States: hidden (204/no data), loading (skeleton), stale-badge, rendered.
 * Handle 204 gracefully: hide panel, không báo lỗi.
 */

import { el, showToast } from '../../utils/dom.js';
import { esc }           from '../../utils/format.js';
import { getJson }       from '../../api/client.js';

const PANEL_ID = 'intelligencePanel';

// ─── Public API ────────────────────────────────────────────────────────────

export async function loadIntelligencePanel() {
  const panel = el(PANEL_ID);
  if (!panel) return;

  panel.innerHTML = _skeletonHTML();
  panel.classList.remove('hidden');

  try {
    // 204 → getJson trả về null hoặc {} rỗng
    const data = await getJson('/api/v1/readmodel/dashboard/intelligence').catch(() => null);

    if (!data || (!data.overall_verdict && !data.market_context)) {
      // Engine chưa chạy lần nào → ẩn panel, không báo lỗi
      panel.classList.add('hidden');
      return;
    }

    panel.innerHTML = _renderHTML(data);
  } catch {
    panel.classList.add('hidden');
  }
}

// ─── Render ─────────────────────────────────────────────────────────────────

function _renderHTML(d) {
  const verdictClass = _verdictClass(d.overall_verdict);
  const confidence   = typeof d.confidence === 'number'
    ? Math.round(d.confidence * 100)
    : typeof d.confidence === 'number' ? d.confidence : null;

  const staleHtml = d.is_stale
    ? `<span class="intel-stale-badge">⚠ Dữ liệu cũ</span>`
    : '';

  const generatedAt = d.generated_at
    ? `<span class="intel-ts">${_relTime(d.generated_at)}</span>`
    : '';

  const priorityHtml = Array.isArray(d.priority_actions) && d.priority_actions.length
    ? `<div class="intel-section">
        <div class="intel-section-label">Ưu tiên hành động</div>
        <ul class="intel-action-list">
          ${d.priority_actions.map((a, i) => `
            <li class="intel-action-item">
              <span class="intel-action-num">${i + 1}</span>
              <span class="intel-action-text">${esc(typeof a === 'string' ? a : a.action ?? a.text ?? JSON.stringify(a))}</span>
            </li>`).join('')}
        </ul>
      </div>`
    : '';

  const riskHtml = Array.isArray(d.risk_flags) && d.risk_flags.length
    ? `<div class="intel-section">
        <div class="intel-section-label">Risk Flags</div>
        <div class="intel-chips">
          ${d.risk_flags.map(r => `<span class="intel-chip intel-chip--risk">${esc(typeof r === 'string' ? r : r.flag ?? r.label ?? JSON.stringify(r))}</span>`).join('')}
        </div>
      </div>`
    : '';

  const watchHtml = Array.isArray(d.watch_list) && d.watch_list.length
    ? `<div class="intel-section">
        <div class="intel-section-label">Cần theo dõi</div>
        <div class="intel-chips">
          ${d.watch_list.map(w => `<span class="intel-chip intel-chip--watch">${esc(typeof w === 'string' ? w : w.ticker ?? w.symbol ?? JSON.stringify(w))}</span>`).join('')}
        </div>
      </div>`
    : '';

  const marketCtxHtml = d.market_context
    ? `<div class="intel-section">
        <div class="intel-section-label">Bối cảnh thị trường</div>
        <p class="intel-market-ctx">${esc(d.market_context)}</p>
      </div>`
    : '';

  const confidenceHtml = confidence !== null
    ? `<div class="intel-confidence" title="Confidence ${confidence}%">
        <div class="intel-conf-bar">
          <div class="intel-conf-fill" style="width:${confidence}%"></div>
        </div>
        <span class="intel-conf-label">${confidence}%</span>
      </div>`
    : '';

  return `
    <div class="intel-header">
      <div class="intel-title-row">
        <span class="intel-icon" aria-hidden="true">🧠</span>
        <h2 class="intel-title">Intelligence Snapshot</h2>
        ${staleHtml}
        ${generatedAt}
      </div>
      <div class="intel-verdict-row">
        <span class="intel-verdict ${verdictClass}">${esc(d.overall_verdict ?? '—')}</span>
        ${confidenceHtml}
      </div>
    </div>
    <div class="intel-body">
      ${marketCtxHtml}
      ${priorityHtml}
      ${riskHtml}
      ${watchHtml}
    </div>`;
}

function _skeletonHTML() {
  return `
    <div class="intel-header">
      <div class="intel-title-row">
        <span class="intel-icon" aria-hidden="true">🧠</span>
        <h2 class="intel-title">Intelligence Snapshot</h2>
      </div>
      <div class="intel-verdict-row">
        <span class="intel-skel intel-skel--verdict"></span>
        <span class="intel-skel intel-skel--bar"></span>
      </div>
    </div>
    <div class="intel-body">
      <div class="intel-skel intel-skel--line"></div>
      <div class="intel-skel intel-skel--line intel-skel--short"></div>
      <div class="intel-skel intel-skel--line"></div>
    </div>`;
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function _verdictClass(verdict) {
  if (!verdict) return '';
  const v = verdict.toLowerCase();
  if (v.includes('bullish') || v.includes('tích cực') || v.includes('mua'))  return 'intel-verdict--bull';
  if (v.includes('bearish') || v.includes('tiêu cực') || v.includes('bán'))  return 'intel-verdict--bear';
  if (v.includes('neutral') || v.includes('trung lập'))                       return 'intel-verdict--neutral';
  if (v.includes('watch') || v.includes('theo dõi'))                          return 'intel-verdict--watch';
  return 'intel-verdict--default';
}

function _relTime(iso) {
  try {
    const diffMin = (Date.now() - new Date(iso).getTime()) / 60_000;
    if (diffMin < 1)   return '< 1 phút trước';
    if (diffMin < 60)  return `${Math.round(diffMin)} phút trước`;
    const diffH = diffMin / 60;
    if (diffH < 24)    return `${Math.round(diffH)}h trước`;
    return `${Math.round(diffH / 24)} ngày trước`;
  } catch {
    return '';
  }
}
