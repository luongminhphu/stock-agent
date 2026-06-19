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
import { getJson, sendJson, coreApiBase } from '../../api/client.js';

const PANEL_ID = 'intelligencePanel';

// ─── Verdict metadata ────────────────────────────────────────────────────────

const VERDICT_META = {
  BUY_SIGNAL:    { cls: 'iv--buy',    icon: '▲', label: 'Mua vào',          sub: 'Tín hiệu mua xuất hiện — xem xét mở vị thế' },
  SELL_SIGNAL:   { cls: 'iv--sell',   icon: '▼', label: 'Bán ra',           sub: 'Tín hiệu bán xuất hiện — xem xét cắt lỗ / chốt lời' },
  RISK_ALERT:    { cls: 'iv--risk',   icon: '⚠', label: 'Cảnh báo rủi ro', sub: 'Phát hiện rủi ro — cần kiểm tra danh mục' },
  REVIEW_THESIS: { cls: 'iv--review', icon: '⟳', label: 'Xem lại thesis',  sub: 'Có thesis cần review hoặc cập nhật luận điểm' },
  HOLD:          { cls: 'iv--hold',   icon: '◆', label: 'Giữ nguyên',       sub: 'Không có tín hiệu mới — duy trì trạng thái hiện tại' },
  NO_ACTION:     { cls: 'iv--none',   icon: '—', label: 'Không hành động',  sub: 'Hệ thống không phát hiện tín hiệu đáng chú ý' },
};

const CONVICTION_META = {
  high:   { cls: 'ic--high',   label: 'Tin cậy cao' },
  medium: { cls: 'ic--medium', label: 'Tin cậy trung bình' },
  low:    { cls: 'ic--low',    label: 'Tin cậy thấp' },
};

const URGENCY_META = {
  high:   { cls: 'iu--high',   dot: '!',  label: 'Cao' },
  medium: { cls: 'iu--medium', dot: '·',  label: 'Trung bình' },
  low:    { cls: 'iu--low',    dot: '·',  label: 'Thấp' },
};

const SEVERITY_META = {
  high:   { cls: 'ir--high',   icon: '▲', label: 'Cao' },
  medium: { cls: 'ir--medium', icon: '◆', label: 'Trung bình' },
  low:    { cls: 'ir--low',    icon: '●', label: 'Thấp' },
};

// ─── Ticker extraction regex (VN stock: 2-4 uppercase letters) ────────────────
const TICKER_RE = /\b([A-Z]{2,4})\b/g;
// Common words to exclude from ticker extraction
const TICKER_EXCLUDE = new Set([
  'AI','ML','OK','NO','ID','PM','MA','PE','RR','KPI','EOD',
  'VN','HN','SX','VIP','CEO','CFO','CTO','IPO','NAV',
]);

// ─── Public API ──────────────────────────────────────────────────────────────

export async function loadIntelligencePanel() {
  const panel = el(PANEL_ID);
  if (!panel) return;

  panel.innerHTML = _skeletonHTML();
  panel.classList.remove('hidden');

  try {
    const data = await getJson('/api/v1/readmodel/dashboard/intelligence').catch(() => null);

    if (!data || (!data.overall_verdict && !data.top_verdict && !data.market_context)) {
      panel.classList.add('hidden');
      return;
    }

    panel.innerHTML = _renderHTML(data);
    _wireFeedback(panel);
    _wireActionExpand(panel);
  } catch {
    panel.classList.add('hidden');
  }
}

// ─── Feedback wiring ─────────────────────────────────────────────────────────

function _wireFeedback(panel) {
  const bar = panel.querySelector('.intel-feedback');
  if (!bar) return;

  bar.addEventListener('click', async (e) => {
    const btn = e.target.closest('.intel-fb-btn');
    if (!btn) return;

    const outcome        = btn.dataset.outcome;
    const verdictEventId = bar.dataset.verdictEventId;
    const verdict        = bar.dataset.verdict;

    bar.querySelectorAll('.intel-fb-btn').forEach(b => b.classList.remove('intel-fb-btn--active'));
    btn.classList.add('intel-fb-btn--active');
    btn.disabled = true;

    try {
      await sendJson(`${coreApiBase()}/feedback`, 'POST', {
        verdict_event_id: verdictEventId,
        verdict,
        outcome,
        trigger_source: 'api',
      });
      showToast('✅ Đã ghi nhận phản hồi');
    } catch (err) {
      btn.classList.remove('intel-fb-btn--active');
      btn.disabled = false;
      showToast(`Lỗi ghi nhận phản hồi: ${err.message}`, 'error');
    }
  });
}

// ─── Action expand wiring ────────────────────────────────────────────────────

function _wireActionExpand(panel) {
  panel.querySelectorAll('.ia-item[data-reasoning]').forEach(item => {
    item.addEventListener('click', () => {
      item.classList.toggle('ia-item--open');
    });
  });
}

// ─── Main render ─────────────────────────────────────────────────────────────

function _renderHTML(d) {
  const rawVerdict = d.top_verdict || d.overall_verdict || 'NO_ACTION';
  const meta       = VERDICT_META[rawVerdict] || VERDICT_META.NO_ACTION;
  const conv       = CONVICTION_META[(d.conviction || 'medium').toLowerCase()] || CONVICTION_META.medium;
  const confidence = typeof d.confidence === 'number' ? Math.round(d.confidence * 100) : null;

  const tsLabel  = d.generated_at ? _fmtTs(d.generated_at) : '';
  const staleBadge = d.is_stale
    ? `<span class="intel-stale">⚠ Dữ liệu cũ</span>` : '';

  const actionsArr  = Array.isArray(d.priority_actions) ? d.priority_actions : [];
  const riskArr     = Array.isArray(d.risk_flags)        ? d.risk_flags       : [];
  const watchArr    = Array.isArray(d.watch_list)        ? d.watch_list       : [];

  // ── Hero ──────────────────────────────────────────────────────────────────
  const confFill  = confidence !== null ? confidence : 0;
  const confColor = confFill >= 70 ? 'var(--success)' : confFill >= 40 ? 'var(--warning)' : 'var(--danger)';

  const heroHTML = `
    <div class="intel-hero">
      <div class="intel-hero-left">
        <div class="intel-verdict-badge ${meta.cls}">
          <span class="ivb-icon">${meta.icon}</span>
          <span class="ivb-label">${meta.label}</span>
        </div>
        <div class="intel-hero-meta">
          <span class="intel-conviction ${conv.cls}">${conv.label}</span>
          <span class="intel-verdict-sub">${esc(meta.sub)}</span>
        </div>
      </div>
      <div class="intel-hero-right">
        ${confidence !== null ? `
        <div class="intel-conf-block">
          <div class="intel-conf-arc" style="--pct:${confFill};--clr:${confColor}">
            <span class="intel-conf-num">${confFill}%</span>
          </div>
          <span class="intel-conf-label">Độ tin cậy</span>
        </div>` : ''}
      </div>
    </div>`;

  // ── Stat bar ──────────────────────────────────────────────────────────────
  const statBarHTML = _renderStatBar(actionsArr, riskArr, watchArr, d);

  // ── Narrative ─────────────────────────────────────────────────────────────
  const narrativeHTML = _renderNarrative(d.narrative_summary || d.market_context || '');

  // ── Actions ───────────────────────────────────────────────────────────────
  const actionsHTML = _renderActions(actionsArr);

  // ── Risk flags ────────────────────────────────────────────────────────────
  const riskHTML = _renderRiskFlags(riskArr);

  // ── Watch tickers ─────────────────────────────────────────────────────────
  const watchHTML = _renderWatchList(watchArr);

  // ── Feedback bar ─────────────────────────────────────────────────────────
  const verdictEventId = d.generated_at ?? `intel-${Date.now()}`;
  const feedbackHTML = `
    <div class="intel-feedback" data-verdict-event-id="${esc(String(verdictEventId))}" data-verdict="${esc(rawVerdict)}">
      <span class="intel-feedback-label">Verdict này:</span>
      <button class="intel-fb-btn" data-outcome="correct">✅ Đúng</button>
      <button class="intel-fb-btn" data-outcome="incorrect">❌ Sai</button>
      <button class="intel-fb-btn" data-outcome="not_acted">⏸ Không hành động</button>
    </div>`;

  return `
    <div class="intel-header">
      <div class="intel-title-row">
        <span class="intel-icon" aria-hidden="true">🧠</span>
        <h2 class="intel-title">AI Phân tích</h2>
        ${staleBadge}
        ${tsLabel ? `<span class="intel-ts">Cập nhật ${esc(tsLabel)}</span>` : ''}
      </div>
    </div>
    ${heroHTML}
    ${statBarHTML}
    <div class="intel-body">
      ${narrativeHTML}
      ${actionsHTML}
      ${riskHTML}
      ${watchHTML}
    </div>
    ${feedbackHTML}`;
}

// ─── Stat bar ─────────────────────────────────────────────────────────────────

function _renderStatBar(actions, risks, watches, d) {
  const items = [
    {
      val: actions.length,
      label: 'Hành động',
      cls: actions.length > 0 ? 'isb--active' : 'isb--zero',
      icon: '⚡',
    },
    {
      val: risks.length,
      label: 'Rủi ro',
      cls: risks.length > 0 ? 'isb--risk' : 'isb--zero',
      icon: '⚠',
    },
    {
      val: watches.length,
      label: 'Theo dõi',
      cls: watches.length > 0 ? 'isb--watch' : 'isb--zero',
      icon: '👁',
    },
  ];

  const cells = items.map(it => `
    <div class="isb-cell ${it.cls}">
      <span class="isb-val">${it.val}</span>
      <span class="isb-label">${it.icon} ${it.label}</span>
    </div>`).join('');

  return `<div class="intel-stat-bar">${cells}</div>`;
}

// ─── Narrative ────────────────────────────────────────────────────────────────

function _renderNarrative(text) {
  if (!text || !text.trim()) return '';

  // Highlight ticker mentions — wrap in <mark class="it-ticker">
  const highlighted = _highlightTickers(esc(text));

  return `
    <div class="intel-narrative">
      <div class="intel-narrative-eyebrow">Phân tích</div>
      <p class="intel-narrative-text">${highlighted}</p>
    </div>`;
}

// ─── Priority actions ─────────────────────────────────────────────────────────

function _renderActions(actions) {
  if (!Array.isArray(actions) || !actions.length) {
    return `
      <div class="intel-section">
        <div class="intel-section-label">
          <span>Hành động ưu tiên</span>
          <span class="intel-section-count">0</span>
        </div>
        <div class="intel-empty-state">
          <span class="intel-empty-icon">◎</span>
          <span class="intel-empty-text">Không có tín hiệu hành động trong chu kỳ này</span>
        </div>
      </div>`;
  }

  const items = actions.map((a, i) => {
    const text      = typeof a === 'string' ? a : (a.action_text || a.action || a.text || '');
    const ticker    = typeof a === 'object' ? (a.ticker || '') : '';
    const reasoning = typeof a === 'object' ? (a.reasoning || '') : '';
    const urgencyKey= typeof a === 'object' ? (a.urgency || 'medium').toLowerCase() : 'medium';
    const urgency   = URGENCY_META[urgencyKey] || URGENCY_META.medium;
    const hasReason = reasoning.length > 0;
    const isHigh    = urgencyKey === 'high';

    return `
      <div class="ia-item ${urgency.cls}${hasReason ? ' ia-item--expandable' : ''}"
           ${hasReason ? `data-reasoning="${esc(reasoning)}"` : ''}
           role="${hasReason ? 'button' : 'listitem'}"
           tabindex="${hasReason ? 0 : -1}">
        <div class="ia-item-main">
          <span class="ia-num ${isHigh ? 'ia-num--high' : ''}">${i + 1}</span>
          <div class="ia-content">
            ${ticker ? `<span class="ia-ticker">${esc(ticker)}</span>` : ''}
            <span class="ia-text">${esc(text)}</span>
          </div>
          <div class="ia-right">
            ${isHigh ? `<span class="ia-urgency-badge">Cao</span>` : ''}
            ${hasReason ? `<span class="ia-expand-icon" aria-hidden="true">›</span>` : ''}
          </div>
        </div>
        ${hasReason ? `
        <div class="ia-reasoning">
          <span class="ia-reasoning-label">Lý do</span>
          ${_highlightTickers(esc(reasoning))}
        </div>` : ''}
      </div>`;
  }).join('');

  return `
    <div class="intel-section">
      <div class="intel-section-label">
        <span>Hành động ưu tiên</span>
        <span class="intel-section-count">${actions.length}</span>
      </div>
      <div class="ia-list" role="list">${items}</div>
    </div>`;
}

// ─── Risk flags ───────────────────────────────────────────────────────────────

function _renderRiskFlags(flags) {
  if (!Array.isArray(flags) || !flags.length) return '';

  // Sort: high first
  const sorted = [...flags].sort((a, b) => {
    const order = { high: 0, medium: 1, low: 2 };
    const sa = order[(typeof a === 'object' ? a.severity : 'low') || 'low'] ?? 2;
    const sb = order[(typeof b === 'object' ? b.severity : 'low') || 'low'] ?? 2;
    return sa - sb;
  });

  const items = sorted.map(f => {
    const desc   = typeof f === 'string' ? f : (f.description || f.flag || f.label || String(f));
    const sevKey = typeof f === 'object' ? (f.severity || 'low').toLowerCase() : 'low';
    const sev    = SEVERITY_META[sevKey] || SEVERITY_META.low;

    return `
      <div class="ir-item ${sev.cls}">
        <div class="ir-header">
          <span class="ir-sev-badge ir-sev--${sevKey}">
            <span class="ir-sev-icon">${sev.icon}</span>
            <span class="ir-sev-label">${sev.label}</span>
          </span>
        </div>
        <p class="ir-text">${_highlightTickers(esc(desc))}</p>
      </div>`;
  }).join('');

  const highCount = sorted.filter(f =>
    (typeof f === 'object' ? f.severity : 'low') === 'high'
  ).length;

  return `
    <div class="intel-section">
      <div class="intel-section-label">
        <span>Tín hiệu rủi ro</span>
        <span class="intel-section-count intel-section-count--risk">${flags.length}</span>
        ${highCount > 0 ? `<span class="intel-high-badge">${highCount} nghiêm trọng</span>` : ''}
      </div>
      <div class="ir-list">${items}</div>
    </div>`;
}

// ─── Watch list ───────────────────────────────────────────────────────────────

function _renderWatchList(tickers) {
  if (!Array.isArray(tickers) || !tickers.length) return '';

  const chips = tickers.map(t => {
    const sym = String(t).toUpperCase();
    return `
      <span class="intel-watch-chip" title="Theo dõi ${sym}">
        <span class="iwc-icon">👁</span>
        <span class="iwc-sym">${esc(sym)}</span>
      </span>`;
  }).join('');

  return `
    <div class="intel-section intel-section--watch">
      <div class="intel-section-label">
        <span>Cần theo dõi tiếp</span>
        <span class="intel-section-count">${tickers.length}</span>
      </div>
      <div class="intel-watch-chips">${chips}</div>
    </div>`;
}

// ─── Skeleton ─────────────────────────────────────────────────────────────────

function _skeletonHTML() {
  return `
    <div class="intel-header">
      <div class="intel-title-row">
        <span class="intel-icon" aria-hidden="true">🧠</span>
        <h2 class="intel-title">AI Phân tích</h2>
      </div>
    </div>
    <div class="intel-hero intel-hero--skel">
      <div class="intel-skel iv-skel--badge"></div>
      <div class="intel-skel iv-skel--arc"></div>
    </div>
    <div class="intel-skel intel-skel--statbar"></div>
    <div class="intel-body">
      <div class="intel-skel iv-skel--block"></div>
      <div class="intel-skel iv-skel--line"></div>
      <div class="intel-skel iv-skel--line iv-skel--short"></div>
    </div>`;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Wrap uppercase 2-4 letter words that look like VN stock tickers with a mark.
 * Operates on already-escaped HTML, so we do text-only replacement.
 */
function _highlightTickers(html) {
  return html.replace(TICKER_RE, (match) => {
    if (TICKER_EXCLUDE.has(match)) return match;
    return `<mark class="it-ticker">${match}</mark>`;
  });
}

function _fmtTs(iso) {
  try {
    const d      = new Date(iso);
    const diffMs = Date.now() - d.getTime();
    const diffMin= diffMs / 60_000;
    if (diffMin < 1)   return '< 1 phút trước';
    if (diffMin < 60)  return `${Math.round(diffMin)} phút trước`;
    const h = diffMin / 60;
    if (h < 24)        return `${Math.round(h)}h trước`;
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    return `${hh}:${mm} ${d.getDate()}/${d.getMonth() + 1}/${String(d.getFullYear()).slice(2)}`;
  } catch { return ''; }
}
