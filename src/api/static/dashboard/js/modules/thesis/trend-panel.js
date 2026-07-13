/**
 * trend-panel.js — Trend Analysis panel for thesis detail sidebar
 * Owner  : dashboard / thesis adapter
 * API    : GET /api/v1/trend/ticker/{ticker}
 * HTML   : #dtab-trend  →  lazy-loaded slot inside thesis detail tab
 *
 * Renders:
 *   1. AI synthesis verdict (BULLISH / NEUTRAL / BEARISH) + action + confidence
 *   2. RRG position badge + trail pattern
 *   3. Indicator grid: RSI, MACD histogram, CMF, ADX
 *   4. Per-indicator notes from AI
 *   5. Next watch conditions
 */

import { getJson } from '../../api/client.js';

const _apiBase = () => {
  const m = window.location.pathname.match(/^(\/[^/]+)?\/dashboard/);
  return m ? `${m[1] || ''}/api/v1` : '/api/v1';
};

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Map verdict/action → CSS class suffix */
function verdictCls(v) {
  switch ((v || '').toUpperCase()) {
    case 'BULLISH':    return 'leading';    // green
    case 'BEARISH':    return 'lagging';    // red
    default:           return 'weakening';  // yellow = neutral
  }
}

function actionCls(a) {
  switch ((a || '').toUpperCase()) {
    case 'ACCUMULATE': return 'success';
    case 'REDUCE':
    case 'AVOID':      return 'danger';
    default:           return 'warning';
  }
}

/** RSI gauge bar (0-100) with overbought/oversold zones */
function rsiBar(rsi) {
  const val = Math.max(0, Math.min(100, rsi));
  let barCls = 'trend-bar--neutral';
  if (val >= 70) barCls = 'trend-bar--overbought';
  else if (val <= 30) barCls = 'trend-bar--oversold';
  else if (val >= 55) barCls = 'trend-bar--bullish';
  else if (val <= 45) barCls = 'trend-bar--bearish';

  return `
    <div class="trend-gauge">
      <div class="trend-gauge-track">
        <div class="trend-gauge-fill ${barCls}" style="width:${val}%"></div>
        <div class="trend-gauge-zone trend-gauge-zone--ob" title="Overbought (>70)"></div>
        <div class="trend-gauge-zone trend-gauge-zone--os" title="Oversold (<30)"></div>
      </div>
      <div class="trend-gauge-labels">
        <span class="trend-gauge-label">0</span>
        <span class="trend-gauge-label trend-gauge-label--mid">50</span>
        <span class="trend-gauge-label">100</span>
      </div>
    </div>`;
}

/** MACD histogram mini-bar */
function macdBar(hist, cross) {
  const maxAbs = Math.max(Math.abs(hist), 0.01);
  const pct = Math.min(100, (Math.abs(hist) / (maxAbs * 2)) * 100 + 50);
  const isBull = hist >= 0;
  const crossLabel = (cross || '').replace('_', ' ');
  return `
    <div class="trend-macd">
      <div class="trend-macd-bar ${isBull ? 'trend-macd-bar--bull' : 'trend-macd-bar--bear'}"
           style="width:${pct}%; ${isBull ? 'margin-left:50%' : `margin-left:${100-pct}%`}">
      </div>
      <span class="trend-macd-cross ${isBull ? 'trend-cross--bull' : 'trend-cross--bear'}">${esc(crossLabel)}</span>
    </div>`;
}

/** CMF bar (-1 to +1) */
function cmfBar(cmf) {
  const val = Math.max(-1, Math.min(1, cmf));
  const pct = ((val + 1) / 2) * 100;          // 0% = -1, 50% = 0, 100% = +1
  const isBull = val >= 0;
  return `
    <div class="trend-cmf">
      <div class="trend-cmf-bar ${isBull ? 'trend-cmf-bar--bull' : 'trend-cmf-bar--bear'}"
           style="${isBull ? `left:50%;width:${(val)*50}%` : `left:${pct}%;width:${(Math.abs(val))*50}%`}">
      </div>
      <div class="trend-cmf-zero"></div>
    </div>`;
}

/** ADX strength badge */
function adxBadge(adx, plusDi, minusDi) {
  let strength, cls;
  if (adx >= 40)       { strength = 'Mạnh';         cls = 'leading'; }
  else if (adx >= 25)  { strength = 'Trending';      cls = adxCls(plusDi, minusDi); }
  else if (adx >= 15)  { strength = 'Yếu';           cls = 'weakening'; }
  else                 { strength = 'Không có trend'; cls = 'lagging'; }

  const dir = plusDi > minusDi ? '↑' : '↓';
  const dirCls = plusDi > minusDi ? 'trend-adx--up' : 'trend-adx--down';
  return `<span class="rrg-badge rrg-q--${cls}">${esc(strength)}</span>
          <span class="trend-adx-dir ${dirCls}">${dir} +DI ${plusDi.toFixed(1)} / -DI ${minusDi.toFixed(1)}</span>`;
}

function adxCls(plusDi, minusDi) {
  return plusDi > minusDi ? 'leading' : 'lagging';
}

// ─────────────────────────────────────────────────────────────────────────────
// Main render
// ─────────────────────────────────────────────────────────────────────────────

function renderTrendPanel(data) {
  const { rrg = {}, indicators = {}, synthesis = {} } = data;
  const macd   = indicators.macd   || {};
  const adxObj = indicators.adx    || {};
  const s      = synthesis;

  const confPct = Math.round((s.confidence || 0) * 100);
  const vCls    = verdictCls(s.verdict);
  const aCls    = actionCls(s.action);

  return `
<div class="trend-panel">

  <!-- ── Verdict header ──────────────────────────────────────────────────── -->
  <div class="trend-verdict-row">
    <div class="trend-verdict-main">
      <span class="rrg-badge rrg-q--${vCls} trend-verdict-badge">${esc(s.verdict || 'N/A')}</span>
      <span class="tl-chip trend-action-chip trend-action--${aCls}">${esc(s.action || 'N/A')}</span>
      <span class="tl-chip trend-conf-chip">Conf ${confPct}%</span>
    </div>
    <p class="trend-signal-summary">${esc(s.signal_summary || '')}</p>
  </div>

  <!-- ── RRG position ────────────────────────────────────────────────────── -->
  <div class="trend-section">
    <div class="trend-section-header">
      <span class="trend-section-title">RRG</span>
      <span class="rrg-badge rrg-q--${verdictCls(rrg.quadrant === 'leading' || rrg.quadrant === 'improving' ? 'BULLISH' : rrg.quadrant === 'weakening' ? 'NEUTRAL' : 'BEARISH')} trend-rrg-quad">${esc(rrg.quadrant || 'N/A')}</span>
      <span class="tl-chip">${esc(rrg.trail_pattern || '')}</span>
    </div>
    <div class="trend-rrg-metrics">
      <span class="trend-metric">RS-Ratio <strong>${(rrg.rs_ratio || 100).toFixed(1)}</strong></span>
      <span class="trend-metric">RS-Momentum <strong>${(rrg.rs_momentum || 100).toFixed(1)}</strong></span>
    </div>
    ${s.rrg_note ? `<p class="trend-ind-note">${esc(s.rrg_note)}</p>` : ''}
  </div>

  <!-- ── RSI ──────────────────────────────────────────────────────────────── -->
  <div class="trend-section">
    <div class="trend-section-header">
      <span class="trend-section-title">RSI (14)</span>
      <strong class="trend-ind-value">${(indicators.rsi || 50).toFixed(1)}</strong>
    </div>
    ${rsiBar(indicators.rsi || 50)}
    ${s.rsi_note ? `<p class="trend-ind-note">${esc(s.rsi_note)}</p>` : ''}
  </div>

  <!-- ── MACD ─────────────────────────────────────────────────────────────── -->
  <div class="trend-section">
    <div class="trend-section-header">
      <span class="trend-section-title">MACD (12/26/9)</span>
      <strong class="trend-ind-value ${macd.histogram >= 0 ? 'trend-val--bull' : 'trend-val--bear'}"
              title="Histogram">Hist ${(macd.histogram || 0).toFixed(2)}</strong>
    </div>
    ${macdBar(macd.histogram || 0, macd.cross || '')}
    ${s.macd_note ? `<p class="trend-ind-note">${esc(s.macd_note)}</p>` : ''}
  </div>

  <!-- ── CMF ──────────────────────────────────────────────────────────────── -->
  <div class="trend-section">
    <div class="trend-section-header">
      <span class="trend-section-title">CMF (20)</span>
      <strong class="trend-ind-value ${(indicators.cmf || 0) >= 0 ? 'trend-val--bull' : 'trend-val--bear'}">${(indicators.cmf || 0).toFixed(3)}</strong>
    </div>
    ${cmfBar(indicators.cmf || 0)}
    ${s.cmf_note ? `<p class="trend-ind-note">${esc(s.cmf_note)}</p>` : ''}
  </div>

  <!-- ── ADX ──────────────────────────────────────────────────────────────── -->
  <div class="trend-section">
    <div class="trend-section-header">
      <span class="trend-section-title">ADX (14)</span>
      <strong class="trend-ind-value">${(adxObj.value || 0).toFixed(1)}</strong>
    </div>
    <div class="trend-adx-row">
      ${adxBadge(adxObj.value || 0, adxObj.plus_di || 0, adxObj.minus_di || 0)}
    </div>
    ${s.adx_note ? `<p class="trend-ind-note">${esc(s.adx_note)}</p>` : ''}
  </div>

  <!-- ── Next watch ────────────────────────────────────────────────────────── -->
  ${s.next_watch ? `
  <div class="trend-section trend-section--watch">
    <span class="trend-section-title">👁 Theo dõi tiếp</span>
    <p class="trend-ind-note">${esc(s.next_watch)}</p>
  </div>` : ''}

</div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Entry point
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Load and render trend analysis into #trendPanelSlot-{thesisId}.
 * Called from thesis-service.js via scheduleIdle.
 *
 * @param {string} ticker  - Ticker symbol (e.g. "VNM")
 * @param {number} thesisId
 * @param {Element} detailWrap - thesis detail container
 */
export async function loadTrendPanel(ticker, thesisId, detailWrap) {
  const slot = detailWrap.querySelector(`#trendPanelSlot-${thesisId}`);
  if (!slot) return;

  // Guard: abort if thesis changed while we were waiting
  if (slot.dataset.ticker !== ticker) return;

  slot.innerHTML = '<div class="skel-line" style="height:120px;border-radius:8px;"></div>';

  try {
    const data = await getJson(`${_apiBase()}/trend/ticker/${encodeURIComponent(ticker)}`);

    // Re-check stale
    if (!detailWrap.contains(slot) || slot.dataset.ticker !== ticker) return;

    if (data.error) {
      slot.innerHTML = `<div class="error-banner">Trend: ${esc(data.error)}</div>`;
      return;
    }

    slot.innerHTML = renderTrendPanel(data);
  } catch (err) {
    if (detailWrap.contains(slot)) {
      slot.innerHTML = `<div class="error-banner">Không tải được trend: ${esc(err.message)}</div>`;
    }
  }
}
