// modules/memory/memory-loader.js
// Owner: dashboard adapter (thin wire)
// Responsibility: wire Cluster D HTML IDs ↔ /api/v1/memory/snapshot
// Rule: đừng chứa business logic — fetch qua memory-api.js, render inline helpers
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
      _renderContextSummary(data);
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
  _setKpi('memKpiEpisodes',   '.mem-kpi-value', data.episode_count ?? '\u2014');
  _setKpi('memKpiPatterns',   '.mem-kpi-value', (data.patterns ?? []).length || '\u2014');
  const confPct = data.confidence != null
    ? `${Math.round(data.confidence * 100)}%` : '\u2014';
  _setKpi('memKpiConfidence', '.mem-kpi-value', confPct);
  _setKpi('memKpiBias',       '.mem-kpi-value', (data.bias_warnings ?? []).length || '0');
}

function _setKpi(cardId, selector, value) {
  const el = document.getElementById(cardId)?.querySelector(selector);
  if (el) el.textContent = value;
}

// ---------------------------------------------------------------------------
// Private: context summary
// ---------------------------------------------------------------------------

function _renderContextSummary(data) {
  const wrap = document.getElementById('memContextSummary');
  if (!wrap) return;

  const text = data.context_summary;
  if (!text) { wrap.classList.add('hidden'); return; }

  wrap.classList.remove('hidden');
  const textEl = wrap.querySelector('.mem-context-text');
  if (textEl) {
    textEl.textContent = text;
  } else {
    wrap.innerHTML = `
      <div class="mem-section-title">\ud83d\udca1 T\u00f3m t\u1eaft h\u00e0nh vi</div>
      <p class="mem-context-text">${esc(text)}</p>
    `;
  }
}

// ---------------------------------------------------------------------------
// Private: episodic feed — richer card layout
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
  feed.innerHTML = episodes.map(ep => _episodeCard(ep)).join('');
}

function _episodeCard(ep) {
  const tickers    = (ep.tickers ?? []).join(', ') || ep.ticker || '\u2014';
  const agentLabel = _agentLabel(ep.agent_type ?? '');
  const verdict    = ep.ai_verdict ?? '';
  const conf       = ep.ai_confidence != null ? Math.round(ep.ai_confidence * 100) : null;
  const keyPoint   = _firstLine(ep.ai_key_points);
  const riskSignal = _firstLine(ep.ai_risk_signals);
  const date       = ep.date ?? ep.created_at ?? '';

  // Verdict badge
  const verdictBadge = verdict
    ? `<span class="mem-ep-verdict mem-ep-verdict--${_verdictClass(verdict)}">${esc(verdict)}</span>`
    : '';

  // Confidence bar
  const confBar = conf != null ? `
    <div class="mem-ep-conf">
      <div class="mem-ep-conf-bar"><div class="mem-ep-conf-fill" style="width:${conf}%"></div></div>
      <span class="mem-ep-conf-label">${conf}%</span>
    </div>` : '';

  // Key point line
  const keyPointLine = keyPoint
    ? `<div class="mem-ep-keypoint">\ud83d\udca1 ${esc(keyPoint)}</div>` : '';

  // Risk signal line
  const riskLine = riskSignal
    ? `<div class="mem-ep-risk">\u26a0\ufe0f ${esc(riskSignal)}</div>` : '';

  // Outcome badge
  const outcomeBadge = ep.outcome != null
    ? `<span class="mem-ep-outcome ${_outcomeClass(ep.outcome)}">${Number(ep.outcome) > 0 ? '+' : ''}${ep.outcome}</span>`
    : `<span class="mem-ep-outcome pending">ch\u01b0a c\u00f3 k\u1ebft qu\u1ea3</span>`;

  return `
    <div class="mem-episode-item">
      <div class="mem-ep-header">
        <div class="mem-ep-left">
          <span class="mem-ep-icon">${_actionIcon(ep.action)}</span>
          <div class="mem-ep-title">
            <span class="mem-ep-ticker">${esc(tickers)}</span>
            <span class="mem-ep-agent">${esc(agentLabel)}</span>
          </div>
        </div>
        <div class="mem-ep-right">
          ${verdictBadge}
          ${outcomeBadge}
        </div>
      </div>
      ${confBar}
      ${keyPointLine}
      ${riskLine}
      <div class="mem-ep-footer">
        <span class="mem-ep-date">${esc(date)}</span>
      </div>
    </div>
  `;
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
  _setKpi('memKpiConfidence', '.mem-kpi-value', '\u2014');
  _setKpi('memKpiBias',       '.mem-kpi-value', '0');
  document.getElementById('memContextSummary')?.classList.add('hidden');
}

function _renderError(message) {
  const target = document.getElementById('episodicFeed')?.closest('section');
  if (!target) return;
  const existing = target.querySelector('.mem-load-error');
  if (existing) existing.remove();
  const errEl = document.createElement('div');
  errEl.className = 'mem-empty mem-load-error';
  errEl.style.cssText = 'color:var(--red,#f87171);';
  errEl.innerHTML = `<div class="mem-empty-icon">\u26a0\ufe0f</div><div>${esc(message)}</div>`;
  target.appendChild(errEl);
}

// ---------------------------------------------------------------------------
// Private: helpers
// ---------------------------------------------------------------------------

function _actionIcon(action) {
  return { BUY: '\ud83d\udfe2', SELL: '\ud83d\udd34', HOLD: '\ud83d\udfe1', SKIP: '\u26ab' }[action] ?? '\u26aa';
}

function _verdictClass(verdict) {
  const v = (verdict ?? '').toUpperCase();
  if (v.includes('BUY'))  return 'buy';
  if (v.includes('SELL')) return 'sell';
  if (v.includes('HOLD') || v.includes('WATCH')) return 'hold';
  if (v.includes('SKIP') || v.includes('AVOID')) return 'skip';
  return 'neutral';
}

function _outcomeClass(outcome) {
  if (outcome == null) return 'pending';
  return Number(outcome) > 0 ? 'pos' : Number(outcome) < 0 ? 'neg' : 'pending';
}

function _agentLabel(agentType) {
  const map = {
    thesis_review:   'Ph\u00e2n t\u00edch thesis',
    briefing:        'Morning brief',
    morning_brief:   'Morning brief',
    eod_brief:       'Cu\u1ed1i ng\u00e0y',
    post_mortem:     'Post-mortem',
    watchlist_scan:  'Watchlist scan',
  };
  return map[agentType] ?? agentType;
}

function _firstLine(text) {
  if (!text) return null;
  const line = text.split('\n')[0].trim();
  return line.length > 120 ? line.slice(0, 117) + '\u2026' : line || null;
}
