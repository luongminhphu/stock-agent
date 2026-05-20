// modules/memory/render-memory.js
// Owner: dashboard (readmodel segment)
// Responsibility: render Memory panel UI into #memory-panel
// Rule: không fetch, không chứa business logic — chỉ render HTML

import { fetchMemorySnapshot } from './memory-api.js';
import { bindRefreshButton } from './memory-api.js';

const PANEL_ID = 'memory-panel';

function panel() {
  return document.getElementById(PANEL_ID);
}

// ── Loading state ──────────────────────────────────────────────────────────────────

export function renderMemoryLoading() {
  const el = panel();
  if (!el) return;
  el.innerHTML = `
    <div class="memory-header">
      <h2 class="section-title">🧠 Investor Memory</h2>
    </div>
    <div class="memory-skeleton">
      <div class="skeleton skeleton-bar" style="width:60%;height:1.2rem;margin-bottom:.75rem"></div>
      <div class="skeleton skeleton-bar" style="width:90%;height:.9rem;margin-bottom:.5rem"></div>
      <div class="skeleton skeleton-bar" style="width:80%;height:.9rem;margin-bottom:.5rem"></div>
      <div class="skeleton skeleton-bar" style="width:70%;height:.9rem"></div>
    </div>`;
}

// ── Empty state ─────────────────────────────────────────────────────────────────────

export function renderMemoryEmpty() {
  const el = panel();
  if (!el) return;
  el.innerHTML = `
    <div class="memory-header">
      <h2 class="section-title">🧠 Investor Memory</h2>
      <button class="btn btn-sm" data-memory-refresh>↻ Refresh</button>
    </div>
    <div class="memory-empty">
      <div class="memory-empty-icon">🧠</div>
      <p class="memory-empty-title">Chưa có bộ nhớ</p>
      <p class="memory-empty-desc">
        Hệ thống sẽ tự động tích lũy sau khi bạn sử dụng
        các tính năng phân tích (briefing, thesis, watchlist scan&hellip;).
      </p>
    </div>`;
}

// ── Error state ────────────────────────────────────────────────────────────────────

export function renderMemoryError(message) {
  const el = panel();
  if (!el) return;
  el.innerHTML = `
    <div class="memory-header">
      <h2 class="section-title">🧠 Investor Memory</h2>
      <button class="btn btn-sm" data-memory-refresh>↻ Refresh</button>
    </div>
    <div class="memory-error">
      <span class="memory-error-icon">⚠️</span>
      <span>${message ?? 'Không thể tải bộ nhớ.'}</span>
    </div>`;
}

// ── Main render ──────────────────────────────────────────────────────────────────────

/**
 * Render full memory panel from API response data.
 * @param {{
 *   has_snapshot: boolean,
 *   episode_count: number,
 *   confidence: number,
 *   period_end: string|null,
 *   patterns: string[],
 *   bias_warnings: string[],
 *   market_regime_reads: string[],
 *   context_summary: string|null
 * }} data
 */
export function renderMemoryPanel(data) {
  const el = panel();
  if (!el) return;

  const confPct = Math.round((data.confidence ?? 0) * 100);
  const confColor = confPct >= 70 ? 'var(--color-success)'
    : confPct >= 50 ? 'var(--color-warning)'
    : 'var(--color-error)';
  const confLabel = confPct >= 70 ? 'Cao' : confPct >= 50 ? 'Trung bình' : 'Thấp';

  const patternsHtml = (data.patterns ?? []).length
    ? (data.patterns).map(p => `<li class="memory-list-item">${_esc(p)}</li>`).join('')
    : '<li class="memory-list-item memory-list-empty"><em>Chưa có pattern rõ ràng</em></li>';

  const biasHtml = (data.bias_warnings ?? []).length
    ? (data.bias_warnings).map(w =>
        `<li class="memory-list-item memory-bias-item">⚠️ ${_esc(w)}</li>`
      ).join('')
    : '';

  const regimeHtml = (data.market_regime_reads ?? []).length
    ? `<div class="memory-regime">${data.market_regime_reads.map(_esc).join(' &rsaquo; ')}</div>`
    : '';

  const footerParts = [];
  if (data.episode_count) footerParts.push(`${data.episode_count} episodes`);
  if (data.period_end) footerParts.push(`Cập nhật: ${data.period_end}`);
  const footerHtml = footerParts.length
    ? `<div class="memory-footer">${footerParts.join(' &bull; ')}</div>`
    : '';

  el.innerHTML = `
    <div class="memory-header">
      <h2 class="section-title">🧠 Investor Memory</h2>
      <button class="btn btn-sm" data-memory-refresh>↻ Refresh</button>
    </div>

    ${
      data.has_snapshot ? `
    <div class="memory-meta">
      <div class="memory-confidence">
        <span class="memory-confidence-label">Confidence</span>
        <div class="memory-confidence-bar-wrap">
          <div class="memory-confidence-bar"
               style="width:${confPct}%;background:${confColor}"></div>
        </div>
        <span class="memory-confidence-value" style="color:${confColor}">
          ${confLabel} (${confPct}%)
        </span>
      </div>
    </div>` : ''
    }

    <div class="memory-section">
      <div class="memory-section-title">🔄 Patterns</div>
      <ul class="memory-list">${patternsHtml}</ul>
    </div>

    ${
      biasHtml ? `
    <div class="memory-section">
      <div class="memory-section-title">🧠 Bias warnings</div>
      <ul class="memory-list">${biasHtml}</ul>
    </div>` : ''
    }

    ${regimeHtml}
    ${footerHtml}`;
}

// ── Load + init ────────────────────────────────────────────────────────────────────────

/**
 * Entry point: fetch + render memory panel, wire refresh button.
 * Call once when the Memory tab becomes visible.
 */
export async function loadMemoryPanel() {
  renderMemoryLoading();

  // Wire refresh once — delegate pattern avoids double-bind on re-renders
  bindRefreshButton(result => {
    // Refresh returned new synthesis data — re-render panel in-place
    if (result?.status === 'ok') {
      renderMemoryPanel({
        has_snapshot: true,
        episode_count: null,
        confidence: result.confidence,
        period_end: null,
        patterns: result.patterns ?? [],
        bias_warnings: result.bias_warnings ?? [],
        market_regime_reads: result.market_regime_reads ?? [],
        context_summary: null,
      });
    }
  });

  try {
    const data = await fetchMemorySnapshot();
    if (!data) {
      renderMemoryEmpty();
    } else {
      renderMemoryPanel(data);
    }
  } catch (err) {
    renderMemoryError(err.message);
  }
}

// ── Util ─────────────────────────────────────────────────────────────────────────────

function _esc(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
