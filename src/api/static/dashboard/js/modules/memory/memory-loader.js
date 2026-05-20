// modules/memory/memory-loader.js
// Owner: dashboard adapter (thin wire)
// Responsibility: wire Cluster D HTML IDs ↔ /api/v1/memory/snapshot
// Rule: không chứa business logic — fetch qua memory-api.js, render inline helpers
// Segment: ai/memory (data) + dashboard (adapter)

import { fetchMemorySnapshot, bindRefreshButton } from './memory-api.js';
import { esc } from '../../utils/format.js';

let _refreshWired = false;

// ---------------------------------------------------------------------------
// Public
// ---------------------------------------------------------------------------

export async function loadMemory() {
  _clearSkeletons();

  if (!_refreshWired) {
    // Wire #memRefreshBtn: set data-memory-refresh so bindRefreshButton picks it up
    const btn = document.getElementById('memRefreshBtn');
    if (btn) btn.setAttribute('data-memory-refresh', '');

    bindRefreshButton(() => loadMemory());
    _refreshWired = true;
  }

  try {
    const data = await fetchMemorySnapshot();
    if (!data) {
      _renderEmpty();
    } else {
      _renderKpis(data);
      _renderEpisodic(data);
      _renderPatterns(data);
      _renderBias(data);
    }
  } catch (err) {
    _renderError(err.message);
  }
}

// ---------------------------------------------------------------------------
// Private: skeleton clear
// ---------------------------------------------------------------------------

function _clearSkeletons() {
  ['memKpiEpisodes', 'memKpiPatterns', 'memKpiConfidence', 'memKpiBias']
    .forEach(id => {
      const val = document.getElementById(id)?.querySelector('.mem-kpi-value');
      if (val) val.innerHTML =
        '<span class="mem-skel" style="width:40px;height:1.2rem;display:block;"></span>';
    });
}

// ---------------------------------------------------------------------------
// Private: KPI strip
// ---------------------------------------------------------------------------

function _renderKpis(data) {
  _setKpi('memKpiEpisodes',   '.mem-kpi-value', data.episode_count ?? '—');
  _setKpi('memKpiPatterns',   '.mem-kpi-value', (data.patterns ?? []).length || '—');
  const confPct = data.confidence != null
    ? `${Math.round(data.confidence * 100)}%` : '—';
  _setKpi('memKpiConfidence', '.mem-kpi-value', confPct);
  _setKpi('memKpiBias',       '.mem-kpi-value', (data.bias_warnings ?? []).length || '0');
}

function _setKpi(cardId, selector, value) {
  const el = document.getElementById(cardId)?.querySelector(selector);
  if (el) el.textContent = value;
}

// ---------------------------------------------------------------------------
// Private: episodic feed
// ---------------------------------------------------------------------------

function _renderEpisodic(data) {
  const feed  = document.getElementById('episodicFeed');
  const empty = document.getElementById('episodicEmpty');
  if (!feed) return;

  const episodes = data.episodes ?? [];
  if (!episodes.length) {
    feed.classList.add('hidden');
    empty?.classList.remove('hidden');
    return;
  }

  empty?.classList.add('hidden');
  feed.classList.remove('hidden');
  feed.innerHTML = episodes.map(ep => `
    <div class="mem-episode-item">
      <span class="mem-episode-icon">${_actionIcon(ep.action)}</span>
      <div>
        <div class="mem-episode-desc">${esc(ep.description ?? ep.ticker ?? '—')}</div>
        <div class="mem-episode-meta">${esc(ep.date ?? ep.created_at ?? '')}</div>
      </div>
      <div class="mem-episode-outcome ${_outcomeClass(ep.outcome)}">
        ${ep.outcome != null ? ep.outcome : '…'}
      </div>
    </div>
  `).join('');
}

// ---------------------------------------------------------------------------
// Private: semantic patterns
// ---------------------------------------------------------------------------

function _renderPatterns(data) {
  const list  = document.getElementById('patternsList');
  const empty = document.getElementById('patternsEmpty');
  if (!list) return;

  const patterns = data.patterns ?? [];
  if (!patterns.length) {
    list.classList.add('hidden');
    empty?.classList.remove('hidden');
    return;
  }

  empty?.classList.add('hidden');
  list.classList.remove('hidden');
  list.innerHTML = patterns.map(p => {
    const text = typeof p === 'string' ? p : (p.description ?? '');
    const type = typeof p === 'object' ? (p.type ?? 'PATTERN') : 'PATTERN';
    const conf = typeof p === 'object' && p.confidence != null
      ? Math.round(p.confidence * 100) : null;
    return `
      <div class="mem-pattern-item">
        <div class="mem-pattern-type">${esc(type)}</div>
        <div class="mem-pattern-desc">${esc(text)}</div>
        ${conf != null ? `
        <div class="mem-pattern-footer">
          <div class="mem-confidence-bar">
            <div class="mem-confidence-fill" style="width:${conf}%"></div>
          </div>
          <span class="mem-confidence-label">${conf}%</span>
        </div>` : ''}
      </div>
    `;
  }).join('');
}

// ---------------------------------------------------------------------------
// Private: bias warnings
// ---------------------------------------------------------------------------

function _renderBias(data) {
  const list  = document.getElementById('biasList');
  const empty = document.getElementById('biasEmpty');
  if (!list) return;

  const biases = data.bias_warnings ?? [];
  if (!biases.length) {
    list.classList.add('hidden');
    empty?.classList.remove('hidden');
    return;
  }

  empty?.classList.add('hidden');
  list.classList.remove('hidden');
  list.innerHTML = biases.map(b => {
    const text     = typeof b === 'string' ? b : (b.description ?? b.warning ?? '');
    const severity = typeof b === 'object' ? (b.severity ?? '') : '';
    return `
      <div class="mem-bias-item ${esc(severity)}">
        <div class="mem-bias-text">${esc(text)}</div>
        ${typeof b === 'object' && b.created_at
          ? `<div class="mem-bias-meta">${esc(b.created_at)}</div>` : ''}
      </div>
    `;
  }).join('');
}

// ---------------------------------------------------------------------------
// Private: empty + error states
// ---------------------------------------------------------------------------

function _renderEmpty() {
  ['episodicFeed', 'patternsList', 'biasList'].forEach(id =>
    document.getElementById(id)?.classList.add('hidden'));
  ['episodicEmpty', 'patternsEmpty', 'biasEmpty'].forEach(id =>
    document.getElementById(id)?.classList.remove('hidden'));
  _setKpi('memKpiEpisodes',   '.mem-kpi-value', '0');
  _setKpi('memKpiPatterns',   '.mem-kpi-value', '0');
  _setKpi('memKpiConfidence', '.mem-kpi-value', '—');
  _setKpi('memKpiBias',       '.mem-kpi-value', '0');
}

function _renderError(message) {
  const target = document.getElementById('episodicFeed')?.closest('section');
  if (!target) return;
  const existing = target.querySelector('.mem-load-error');
  if (existing) existing.remove();
  const errEl = document.createElement('div');
  errEl.className = 'mem-empty mem-load-error';
  errEl.style.cssText = 'color:var(--red,#f87171);';
  errEl.innerHTML = `<div class="mem-empty-icon">⚠️</div><div>${esc(message)}</div>`;
  target.appendChild(errEl);
}

// ---------------------------------------------------------------------------
// Private: helpers
// ---------------------------------------------------------------------------

function _actionIcon(action) {
  return { BUY: '🟢', SELL: '🔴', HOLD: '🟡', SKIP: '⚫' }[action] ?? '⚪';
}

function _outcomeClass(outcome) {
  if (outcome == null) return 'pending';
  return Number(outcome) > 0 ? 'pos' : Number(outcome) < 0 ? 'neg' : 'pending';
}
