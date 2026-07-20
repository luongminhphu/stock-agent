// modules/memory/memory-loader.js
// Owner: dashboard adapter (thin wire)
// Responsibility: wire Cluster D HTML IDs ↔ /api/v1/memory/snapshot
// Rule: đừng chứa business logic — fetch qua memory-api.js, render inline helpers
// Segment: ai/memory (data) + dashboard (adapter)

import { fetchMemorySnapshot, fetchBehavioralDNA, bindRefreshButton } from './memory-api.js';
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

  // Load memory snapshot and behavioral DNA in parallel
  const [snapshotResult, dnaResult] = await Promise.allSettled([
    fetchMemorySnapshot(),
    fetchBehavioralDNA(),
  ]);

  const data = snapshotResult.status === 'fulfilled' ? snapshotResult.value : null;
  const dna  = dnaResult.status  === 'fulfilled' ? dnaResult.value  : null;

  if (!data && !dna) {
    _renderEmpty();
  } else {
    if (data) {
      _renderKpis(data);
      _renderContextSummary(data);
      _renderEpisodic(data);
      _renderPatterns(data);
      _renderBias(data);
    }
    _renderBehavioralDNA(dna);
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
// Private: episodic feed — compact single-line card
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

/**
 * Compact single-row episode card:
 *   🟡  PC1 · Watchlist scan    MONITORING · 63%  ⚠ Giá tăng mạnh ~6%   +4.2%   21/05 07:13
 *
 * Design rules:
 * - Chỉ 1 dòng thông tin chính, không conf bar, không keypoint block riêng
 * - Risk signal được sanitize khỏi Python repr (RiskSignal(...))
 * - Outcome hiển thị dạng % với dấu + cho positive; "chưa có kết quả" bị ẩn để tiết kiệm space
 */
function _episodeCard(ep) {
  const tickers    = (ep.tickers ?? []).join(', ') || ep.ticker || '\u2014';
  const agentLabel = _agentLabel(ep.agent_type ?? '');
  const verdict    = _cleanVerdict(ep.ai_verdict ?? '');
  const conf       = ep.ai_confidence != null ? Math.round(ep.ai_confidence * 100) : null;
  const riskSnip   = _extractRiskSnippet(ep.ai_risk_signals);
  const date       = _formatDate(ep.date ?? ep.created_at ?? '');

  const confTag = conf != null
    ? `<span class="mem-ep-conf-tag">${conf}%</span>` : '';

  const verdictTag = verdict
    ? `<span class="mem-ep-verdict mem-ep-verdict--${_verdictClass(verdict)}">${esc(verdict)}</span>`
    : '';

  const riskSnippet = riskSnip
    ? `<span class="mem-ep-risk-snip">\u26a0\ufe0f ${esc(riskSnip)}</span>` : '';

  // Fix: append % unit; handle float rounding for clean display
  const outcomeTag = ep.outcome != null
    ? (() => {
        const val = Number(ep.outcome);
        const sign = val > 0 ? '+' : '';
        const display = Number.isInteger(val) ? val : val.toFixed(2).replace(/\.?0+$/, '');
        return `<span class="mem-ep-outcome ${_outcomeClass(ep.outcome)}">${sign}${display}%</span>`;
      })()
    : '';

  return `
    <div class="mem-episode-item mem-episode-item--compact">
      <span class="mem-ep-icon">${_actionIcon(ep.action)}</span>
      <span class="mem-ep-ticker">${esc(tickers)}</span>
      <span class="mem-ep-agent">${esc(agentLabel)}</span>
      <span class="mem-ep-divider">\u00b7</span>
      ${verdictTag}
      ${confTag}
      ${riskSnippet}
      <span class="mem-ep-spacer"></span>
      ${outcomeTag}
      <span class="mem-ep-date">${esc(date)}</span>
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

// ---------------------------------------------------------------------------
// Private: Behavioral DNA
// ---------------------------------------------------------------------------

function _renderBehavioralDNA(dna) {
  const wrap = document.getElementById('behavioralDnaPanel');
  if (!wrap) return;

  // Skeleton clear
  wrap.querySelectorAll('.dna-skel').forEach(el => el.remove());

  if (!dna || !dna.has_data) {
    wrap.innerHTML = `
      <div class="dna-empty">
        <div class="dna-empty-icon">🧬</div>
        <div class="dna-empty-title">Chưa đủ dữ liệu</div>
        <div class="dna-empty-desc">AI cần ít nhất 3 quyết định đã đánh giá để xây dựng hồ sơ hành vi của bạn. Hồ sơ sẽ tự cập nhật sau 15:15 mỗi ngày.</div>
      </div>`;
    return;
  }

  const pct = v => v != null ? `${Math.round(v * 100)}%` : '—';
  const days = v => v != null ? `${v.toFixed(1)} ngày` : '—';
  const winClass = v => v == null ? '' : v >= 0.55 ? 'dna-val--good' : v <= 0.4 ? 'dna-val--bad' : '';

  // Hold duration ratio
  let holdRatioNote = '';
  if (dna.avg_hold_days_winners != null && dna.avg_hold_days_losers != null) {
    const ratio = dna.avg_hold_days_losers / Math.max(dna.avg_hold_days_winners, 0.1);
    if (ratio > 1.5) holdRatioNote = `<span class="dna-warn">⚠ Giữ loser lâu hơn winner ${ratio.toFixed(1)}x</span>`;
    else if (ratio < 0.7) holdRatioNote = `<span class="dna-good">✓ Cắt loser nhanh hơn winner</span>`;
  }

  // Top patterns
  const patternHtml = (dna.top_patterns || []).slice(0, 5).map(p => `
    <div class="dna-pattern-row">
      <span class="dna-pattern-name">${esc(p.pattern.replace(/_/g, ' '))}</span>
      <span class="dna-pattern-count">×${p.count}</span>
    </div>`).join('') || '<div class="dna-pattern-row dna-empty-row">Chưa có pattern</div>';

  // Day win rates
  const dayMap = dna.day_win_rates || {};
  const dayOrder = ['Monday','Tuesday','Wednesday','Thursday','Friday'];
  const dayLabel = { Monday:'T2', Tuesday:'T3', Wednesday:'T4', Thursday:'T5', Friday:'T6' };
  const dayBars = dayOrder.filter(d => dayMap[d] != null).map(d => {
    const w = Math.round(dayMap[d] * 100);
    const best  = dna.best_decision_day  === d ? 'dna-day--best'  : '';
    const worst = dna.worst_decision_day === d ? 'dna-day--worst' : '';
    return `
      <div class="dna-day-col ${best} ${worst}">
        <div class="dna-day-bar-wrap">
          <div class="dna-day-bar" style="height:${w}%"></div>
        </div>
        <div class="dna-day-label">${dayLabel[d]}</div>
        <div class="dna-day-pct">${w}%</div>
      </div>`;
  }).join('');

  wrap.innerHTML = `
    <div class="dna-header">
      <span class="dna-title">🧬 Behavioral DNA</span>
      <span class="dna-meta">${dna.lookback_days} ngày · ${dna.total_evaluated} giao dịch đánh giá</span>
    </div>

    <div class="dna-kpi-row">
      <div class="dna-kpi">
        <div class="dna-kpi-label">Win rate</div>
        <div class="dna-kpi-value ${winClass(dna.win_rate_overall)}">${pct(dna.win_rate_overall)}</div>
        <div class="dna-kpi-sub">BUY ${pct(dna.win_rate_buy)} · SELL ${pct(dna.win_rate_sell)}</div>
      </div>
      <div class="dna-kpi">
        <div class="dna-kpi-label">Hold winners</div>
        <div class="dna-kpi-value">${days(dna.avg_hold_days_winners)}</div>
        <div class="dna-kpi-sub">Losers ${days(dna.avg_hold_days_losers)}</div>
      </div>
      <div class="dna-kpi">
        <div class="dna-kpi-label">Bán sớm winner</div>
        <div class="dna-kpi-value ${dna.early_exit_winner_rate > 0.5 ? 'dna-val--bad' : ''}">${pct(dna.early_exit_winner_rate)}</div>
        <div class="dna-kpi-sub">Giữ loser quá lâu ${pct(dna.late_exit_loser_rate)}</div>
      </div>
    </div>

    ${holdRatioNote ? `<div class="dna-ratio-note">${holdRatioNote}</div>` : ''}

    <div class="dna-section-row">
      <div class="dna-section dna-section--patterns">
        <div class="dna-section-title">Hành vi lặp lại</div>
        ${patternHtml}
      </div>

      <div class="dna-section dna-section--timing">
        <div class="dna-section-title">Win rate theo thứ</div>
        ${dayBars
          ? `<div class="dna-day-chart">${dayBars}</div>`
          : '<div class="dna-empty-row">Chưa đủ dữ liệu</div>'}
      </div>
    </div>
  `;
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
    thesis_review:   'Thesis',
    briefing:        'Morning',
    morning_brief:   'Morning',
    eod_brief:       'EOD',
    post_mortem:     'Post-mortem',
    watchlist_scan:  'Scan',
    proactive_alert: 'Alert',
  };
  return map[agentType] ?? agentType;
}

/**
 * Clean up ai_verdict — strip leading urgency= prefix if backend leaks it.
 * e.g. "urgency=MONITORING confidence=0.63 ..." → "MONITORING"
 */
function _cleanVerdict(raw) {
  if (!raw) return '';
  // If it looks like key=value pairs, extract urgency or first value
  if (raw.includes('=')) {
    const m = raw.match(/urgency\s*=\s*(\S+)/i);
    if (m) return m[1].replace(/,+$/, '');
    // fallback: grab first value token
    const first = raw.match(/=\s*(\S+)/);
    if (first) return first[1].replace(/,+$/, '');
  }
  // Plain string — truncate if long
  return raw.length > 20 ? raw.slice(0, 18) + '\u2026' : raw;
}

/**
 * Extract a short human-readable snippet from ai_risk_signals.
 * Handles:
 *   - Plain string
 *   - Python repr: [RiskSignal(description='...', ...)]
 *   - JSON array of objects
 */
function _extractRiskSnippet(raw) {
  if (!raw) return null;
  const s = String(raw).trim();
  if (!s || s === '[]' || s === 'null') return null;

  // Python repr: RiskSignal(description='...')
  const reprMatch = s.match(/description\s*=\s*['"]([^'"]{1,100})/i);
  if (reprMatch) return _truncate(reprMatch[1]);

  // JSON array
  try {
    const arr = JSON.parse(s);
    if (Array.isArray(arr) && arr.length) {
      const first = arr[0];
      const desc = typeof first === 'string' ? first : (first.description ?? first.signal ?? '');
      return _truncate(desc);
    }
  } catch (_) { /* not JSON */ }

  // Plain string fallback
  const plain = s.split('\n')[0].trim();
  return plain.length > 2 ? _truncate(plain) : null;
}

function _truncate(str, max = 60) {
  const s = String(str).trim();
  return s.length > max ? s.slice(0, max - 1) + '\u2026' : s || null;
}

/**
 * Format ISO/datetime string → "DD/MM HH:mm"
 */
function _formatDate(raw) {
  if (!raw) return '';
  // Already formatted like "21/05/2026 07:13" → keep DD/MM HH:mm
  const already = raw.match(/(\d{2}\/\d{2})(?:\/\d{4})?\s+(\d{2}:\d{2})/);
  if (already) return `${already[1]} ${already[2]}`;
  // ISO 8601
  try {
    const d = new Date(raw);
    if (isNaN(d)) return raw;
    const dd = String(d.getDate()).padStart(2, '0');
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const hh = String(d.getHours()).padStart(2, '0');
    const mi = String(d.getMinutes()).padStart(2, '0');
    return `${dd}/${mm} ${hh}:${mi}`;
  } catch (_) { return raw; }
}
