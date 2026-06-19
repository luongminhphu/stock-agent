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
  BUY_SIGNAL:    { cls: 'iv--buy',    icon: '▲', label: 'Mua vào' },
  SELL_SIGNAL:   { cls: 'iv--sell',   icon: '▼', label: 'Bán ra' },
  RISK_ALERT:    { cls: 'iv--risk',   icon: '⚠', label: 'Cảnh báo rủi ro' },
  REVIEW_THESIS: { cls: 'iv--review', icon: '⟳', label: 'Xem lại thesis' },
  HOLD:          { cls: 'iv--hold',   icon: '◆', label: 'Giữ nguyên' },
  NO_ACTION:     { cls: 'iv--none',   icon: '—', label: 'Không hành động' },
};

const CONVICTION_META = {
  high:   { cls: 'ic--high',   label: 'Tin cậy cao' },
  medium: { cls: 'ic--medium', label: 'Tin cậy trung bình' },
  low:    { cls: 'ic--low',    label: 'Tin cậy thấp' },
};

const URGENCY_META = {
  high:   { cls: 'iu--high',   dot: '🔴' },
  medium: { cls: 'iu--medium', dot: '🟡' },
  low:    { cls: 'iu--low',    dot: '🔵' },
};

const SEVERITY_META = {
  high:   { cls: 'ir--high',   icon: '●' },
  medium: { cls: 'ir--medium', icon: '●' },
  low:    { cls: 'ir--low',    icon: '●' },
};

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

// ─── Render ──────────────────────────────────────────────────────────────────

function _renderHTML(d) {
  // Normalise verdict — prefer top_verdict (canonical) over overall_verdict
  const rawVerdict = d.top_verdict || d.overall_verdict || 'NO_ACTION';
  const meta       = VERDICT_META[rawVerdict] || VERDICT_META.NO_ACTION;
  const conv       = CONVICTION_META[(d.conviction || 'medium').toLowerCase()] || CONVICTION_META.medium;
  const confidence = typeof d.confidence === 'number' ? Math.round(d.confidence * 100) : null;

  // Timestamp
  const tsLabel = d.generated_at ? _fmtTs(d.generated_at) : '';
  const staleBadge = d.is_stale
    ? `<span class="intel-stale">⚠ Dữ liệu cũ</span>`
    : '';

  // ── Verdict hero ─────────────────────────────────────────────────────────
  const confFill  = confidence !== null ? confidence : 0;
  const confColor = confFill >= 70 ? 'var(--success)' : confFill >= 40 ? 'var(--warning)' : 'var(--danger)';

  const heroHTML = `
    <div class="intel-hero">
      <div class="intel-hero-left">
        <div class="intel-verdict-badge ${meta.cls}" title="${esc(rawVerdict)}">
          <span class="ivb-icon">${meta.icon}</span>
          <span class="ivb-label">${meta.label}</span>
        </div>
        <span class="intel-conviction ${conv.cls}">${conv.label}</span>
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

  // ── Narrative summary ─────────────────────────────────────────────────────
  const summary = d.narrative_summary || d.market_context || '';
  const narrativeHTML = summary ? `
    <div class="intel-narrative">
      <p class="intel-narrative-text">${esc(summary)}</p>
    </div>` : '';

  // ── Priority actions ──────────────────────────────────────────────────────
  const actionsHTML = _renderActions(d.priority_actions);

  // ── Risk flags ────────────────────────────────────────────────────────────
  const riskHTML = _renderRiskFlags(d.risk_flags);

  // ── Watch tickers ─────────────────────────────────────────────────────────
  const watchHTML = _renderWatchList(d.watch_list);

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
        ${tsLabel ? `<span class="intel-ts">${esc(tsLabel)}</span>` : ''}
      </div>
    </div>
    ${heroHTML}
    <div class="intel-body">
      ${narrativeHTML}
      ${actionsHTML}
      ${riskHTML}
      ${watchHTML}
    </div>
    ${feedbackHTML}`;
}

// ─── Section renderers ───────────────────────────────────────────────────────

function _renderActions(actions) {
  if (!Array.isArray(actions) || !actions.length) return '';

  const items = actions.map((a, i) => {
    const text      = typeof a === 'string' ? a : (a.action_text || a.action || a.text || '');
    const ticker    = typeof a === 'object' ? (a.ticker || '') : '';
    const reasoning = typeof a === 'object' ? (a.reasoning || '') : '';
    const urgencyKey= typeof a === 'object' ? (a.urgency || 'medium').toLowerCase() : 'medium';
    const urgency   = URGENCY_META[urgencyKey] || URGENCY_META.medium;
    const hasReason = reasoning.length > 0;

    return `
      <div class="ia-item ${urgency.cls}${hasReason ? ' ia-item--expandable' : ''}" ${hasReason ? `data-reasoning="${esc(reasoning)}"` : ''} role="${hasReason ? 'button' : 'listitem'}" tabindex="${hasReason ? 0 : -1}">
        <div class="ia-item-main">
          <span class="ia-num">${i + 1}</span>
          <div class="ia-content">
            ${ticker ? `<span class="ia-ticker">${esc(ticker)}</span>` : ''}
            <span class="ia-text">${esc(text)}</span>
          </div>
          ${urgencyKey === 'high' ? `<span class="ia-urgency-dot" title="Ưu tiên cao">!</span>` : ''}
          ${hasReason ? `<span class="ia-expand-icon" aria-hidden="true">›</span>` : ''}
        </div>
        ${hasReason ? `<div class="ia-reasoning">${esc(reasoning)}</div>` : ''}
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

function _renderRiskFlags(flags) {
  if (!Array.isArray(flags) || !flags.length) return '';

  const items = flags.map(f => {
    const desc     = typeof f === 'string' ? f : (f.description || f.flag || f.label || String(f));
    const sevKey   = typeof f === 'object' ? (f.severity || 'low').toLowerCase() : 'low';
    const sev      = SEVERITY_META[sevKey] || SEVERITY_META.low;
    return `
      <div class="ir-item ${sev.cls}">
        <span class="ir-dot" aria-hidden="true">${sev.icon}</span>
        <span class="ir-text">${esc(desc)}</span>
      </div>`;
  }).join('');

  return `
    <div class="intel-section">
      <div class="intel-section-label">
        <span>Tín hiệu rủi ro</span>
        <span class="intel-section-count intel-section-count--risk">${flags.length}</span>
      </div>
      <div class="ir-list">${items}</div>
    </div>`;
}

function _renderWatchList(tickers) {
  if (!Array.isArray(tickers) || !tickers.length) return '';
  const chips = tickers.map(t =>
    `<span class="intel-watch-chip">${esc(String(t).toUpperCase())}</span>`
  ).join('');
  return `
    <div class="intel-section intel-section--watch">
      <div class="intel-section-label"><span>Theo dõi tiếp</span></div>
      <div class="intel-watch-chips">${chips}</div>
    </div>`;
}

// ─── Skeleton ────────────────────────────────────────────────────────────────

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
    <div class="intel-body">
      <div class="intel-skel iv-skel--line"></div>
      <div class="intel-skel iv-skel--line iv-skel--short"></div>
      <div class="intel-skel iv-skel--line"></div>
    </div>`;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function _fmtTs(iso) {
  try {
    const d      = new Date(iso);
    const diffMs = Date.now() - d.getTime();
    const diffMin= diffMs / 60_000;
    if (diffMin < 1)   return '< 1 phút trước';
    if (diffMin < 60)  return `${Math.round(diffMin)} phút trước`;
    const h = diffMin / 60;
    if (h < 24)        return `${Math.round(h)}h trước`;
    // Show absolute HH:MM DD/M
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    return `${hh}:${mm} ${d.getDate()}/${d.getMonth() + 1}/${String(d.getFullYear()).slice(2)}`;
  } catch { return ''; }
}
