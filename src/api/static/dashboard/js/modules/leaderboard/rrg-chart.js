/**
 * rrg-chart.js — RRG (Relative Rotation Graph) Canvas renderer
 * Owner  : dashboard / market adapter
 * API    : GET /api/v1/rrg/thesis
 * HTML   : #rrgWrap  →  <canvas id="rrgCanvas">
 * CSS    : css/modules/leaderboard.css  (.rrg-*)
 *
 * Features (Option C — Full UX Overhaul):
 *  C1  ResizeObserver — canvas redraws on wrap resize
 *  C2  Label clamp + velocity arrow at head dot
 *  C3  Hover tooltip (mousemove)
 *  C4  Animated canvas transition (fade old → new)
 *  C5  Aggregate quadrant summary bar
 *  C6  Sidebar detail panel with AI rotation signal
 *  C7  Legend sparklines (32×20 SVG per ticker, clickable)
 *  C8  Compact scrollable filter bar + keyboard ←→ navigation
 *
 * Quadrant layout (centre = 100, 100):
 *   X = RS-Ratio   (right = outperforming)
 *   Y = RS-Momentum (up   = accelerating)
 *   Leading (TR) | Weakening (BR) | Lagging (BL) | Improving (TL)
 *
 * Exports:
 *   loadRRG() — fetch + render; safe to call on every dashboard refresh
 *
 * Key design invariants (DO NOT break on future edits):
 *  - All event listeners are delegated to long-lived container elements
 *    (wrap, bar) — never to children that may be replaced by innerHTML.
 *  - loadRRG() carries a fetch-serial guard: only the latest in-flight
 *    fetch is allowed to commit state. Stale responses are discarded.
 *  - Animation frames are always cancelled before starting a new one.
 *  - Legend listeners are delegated to the legend container, not per-item.
 */

import { getJson }          from '../../api/client.js';
import { loadThesisDetail } from '../thesis/thesis-service.js';

// ─────────────────────────────────────────────────────────────────────────────
// Config
// ─────────────────────────────────────────────────────────────────────────────

const API_URL          = '/api/v1/rrg/thesis';
const ROTATION_API     = '/api/v1/rrg/rotation';
const LOOKBACK_OPTIONS = [26, 52];
const LOOKBACK_KEY     = 'rrg_lookback_weeks';
const EXTRA_KEY        = 'rrg_extra_tickers';
const HIDDEN_KEY       = 'rrg_hidden_tickers';
const MAX_EXTRA        = 10;
const HIT_RADIUS       = 22;

const CANVAS_ID = 'rrgCanvas';
const WRAP_ID   = 'rrgWrap';
const STATUS_ID = 'rrgStatus';
const DETAIL_ID = 'rrgDetail';
const TOOLTIP_ID = 'rrgTooltip';

const Q_COLORS = {
  leading:   { bg: 'rgba(74,222,128,0.07)',  label: 'rgba(74,222,128,0.45)',  strong: '#4ade80' },
  weakening: { bg: 'rgba(251,191,36,0.07)',  label: 'rgba(251,191,36,0.45)',  strong: '#fbbf24' },
  lagging:   { bg: 'rgba(248,113,113,0.07)', label: 'rgba(248,113,113,0.45)', strong: '#f87171' },
  improving: { bg: 'rgba(125,211,252,0.07)', label: 'rgba(125,211,252,0.45)', strong: '#7dd3fc' },
};

const TRAIL_PALETTE = [
  '#7dd3fc', '#4ade80', '#fbbf24', '#f87171',
  '#a78bfa', '#fb923c', '#60a5fa', '#34d399',
  '#f472b6', '#e879f9', '#86efac', '#fde68a',
];

// ─────────────────────────────────────────────────────────────────────────────
// Module state
// ─────────────────────────────────────────────────────────────────────────────

let _allTickers    = [];            // all valid tickers from last fetch
let _hidden        = new Set();     // tickers excluded from canvas
let _asOf          = null;          // date string from API
let _lookbackWeeks = _loadLookback();
let _extraTickers  = _loadExtra();
let _focusedIdx    = -1;            // C8: keyboard nav index
let _headPositions = new Map();     // Map<ticker, {x,y}> CSS px — for hit-test
let _resizeObs     = null;          // C1: singleton ResizeObserver
let _animFrame     = null;          // C4: rAF handle
let _prevImageData = null;          // C4: snapshot for fade
let _activeDetail  = null;          // C6: ticker currently shown in detail panel
let _fetchSerial   = 0;             // in-flight guard: discard stale responses

// ─────────────────────────────────────────────────────────────────────────────
// Persistence helpers
// ─────────────────────────────────────────────────────────────────────────────

function _saveHidden() {
  try { localStorage.setItem(HIDDEN_KEY, JSON.stringify([..._hidden])); } catch (_) {}
}
function _loadHidden(validSet) {
  try {
    const raw = localStorage.getItem(HIDDEN_KEY);
    if (!raw) return new Set();
    const saved   = new Set(JSON.parse(raw));
    const clamped = new Set([...saved].filter(t => validSet.has(t)));
    if (clamped.size >= validSet.size) return new Set(); // would hide everything
    return clamped;
  } catch (_) { return new Set(); }
}
function _saveExtra() {
  try { localStorage.setItem(EXTRA_KEY, JSON.stringify([..._extraTickers])); } catch (_) {}
}
function _loadExtra() {
  try {
    const v = JSON.parse(localStorage.getItem(EXTRA_KEY) ?? '[]');
    return new Set(Array.isArray(v) ? v.slice(0, MAX_EXTRA) : []);
  } catch (_) { return new Set(); }
}
function _saveLookback() {
  try { localStorage.setItem(LOOKBACK_KEY, String(_lookbackWeeks)); } catch (_) {}
}
function _loadLookback() {
  try {
    const v = parseInt(localStorage.getItem(LOOKBACK_KEY), 10);
    return LOOKBACK_OPTIONS.includes(v) ? v : LOOKBACK_OPTIONS[0];
  } catch (_) { return LOOKBACK_OPTIONS[0]; }
}

// ─────────────────────────────────────────────────────────────────────────────
// Public entry point
// ─────────────────────────────────────────────────────────────────────────────

export async function loadRRG() {
  const wrap = document.getElementById(WRAP_ID);
  if (!wrap) return;

  // Cancel any in-flight animation before starting a new fetch
  if (_animFrame) { cancelAnimationFrame(_animFrame); _animFrame = null; }

  // Increment serial — any response from a previous call will be discarded
  const serial = ++_fetchSerial;

  _setStatus('Đang tải RRG…');

  try {
    const extraParam = _extraTickers.size
      ? `&extra=${[..._extraTickers].join(',')}`
      : '';
    const data = await getJson(
      `${API_URL}?lookback_weeks=${_lookbackWeeks}&trail_points=0${extraParam}`
    );

    // Stale response guard — a newer call has already taken over
    if (serial !== _fetchSerial) return;

    const tickers = data?.tickers ?? [];
    if (!tickers.length) { _setStatus('Chưa có thesis active để vẽ RRG.'); return; }

    const valid = tickers.filter(t => t.trail && t.trail.length >= 2);
    if (!valid.length) { _setStatus('Dữ liệu giá chưa đủ để tính RRG (cần ≥ 55 phiên).'); return; }

    // Snapshot current canvas BEFORE updating state (C4 fade)
    _snapshotCanvas();

    // Commit new state
    _allTickers   = valid;
    _hidden       = _loadHidden(new Set(valid.map(t => t.ticker)));
    _asOf         = data.as_of ?? null;
    if (Array.isArray(data.extra_tickers)) _extraTickers = new Set(data.extra_tickers);

    _clearStatus();

    // Build / update all UI zones — order matters
    _renderSummaryBar(wrap);   // C5: above filter bar
    _renderFilterBar(wrap);    // C8: chips + lookback buttons
    _ensureDetailPanel(wrap);  // C6: sidebar (create once)
    _ensureTooltip(wrap);      // C3: tooltip div (create once)
    _wireWrapEvents(wrap);     // canvas click/hover/keyboard (wire once)
    _wireResizeObserver(wrap); // C1: resize (wire once)

    // Sync button active states after rebuilding HTML
    const bar = wrap.querySelector('.rrg-filter-bar');
    if (bar) {
      _syncLookbackBtns(bar);
      _syncChips(bar);
    }

    // C4: draw + animate
    _redrawAnimated(wrap);

  } catch (err) {
    if (serial !== _fetchSerial) return; // stale error — ignore
    _setStatus(`Không tải được RRG: ${err.message}`);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// C4: Canvas transition
// ─────────────────────────────────────────────────────────────────────────────

function _snapshotCanvas() {
  const cv = document.getElementById(CANVAS_ID);
  if (!cv) return;
  try { _prevImageData = cv.toDataURL(); } catch (_) { _prevImageData = null; }
}

function _redrawAnimated(wrap) {
  if (_animFrame) { cancelAnimationFrame(_animFrame); _animFrame = null; }

  const visible = _allTickers.filter(t => !_hidden.has(t.ticker));

  // Draw new frame + update dependent DOM synchronously — not gated by animation
  _drawCanvas(wrap, visible);
  _renderLegend(wrap, visible);
  _setCanvasA11y();

  if (!_prevImageData) return;

  const cv = document.getElementById(CANVAS_ID);
  if (!cv) return;
  const ctx = cv.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const W   = cv.width  / dpr;
  const H   = cv.height / dpr;

  const img = new Image();
  img.src = _prevImageData;
  let alpha = 0.80;

  const step = () => {
    if (alpha <= 0) { _prevImageData = null; _animFrame = null; return; }
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.drawImage(img, 0, 0, W, H);
    ctx.restore();
    alpha -= 0.12;
    _animFrame = requestAnimationFrame(step);
  };
  img.onload  = () => { _animFrame = requestAnimationFrame(step); };
  img.onerror = () => { _prevImageData = null; };
}

// Immediate redraw — no animation (chip toggle, keyboard nav, resize)
function _redraw(wrap) {
  if (_animFrame) { cancelAnimationFrame(_animFrame); _animFrame = null; }
  const visible = _allTickers.filter(t => !_hidden.has(t.ticker));
  _drawCanvas(wrap, visible);
  _renderLegend(wrap, visible);
  _setCanvasA11y();
}

function _setCanvasA11y() {
  const cv = document.getElementById(CANVAS_ID);
  if (!cv) return;
  cv.setAttribute('tabindex', '0');
  cv.setAttribute('role', 'img');
  cv.setAttribute('aria-label', 'Relative Rotation Graph — dùng ←→ để chọn ticker');
}

// ─────────────────────────────────────────────────────────────────────────────
// C5: Summary bar
// ─────────────────────────────────────────────────────────────────────────────

function _renderSummaryBar(wrap) {
  let bar = wrap.querySelector('.rrg-summary-bar');
  if (!bar) {
    bar = document.createElement('div');
    bar.className = 'rrg-summary-bar';
    wrap.prepend(bar);
  }

  const total = _allTickers.length;
  if (!total) { bar.innerHTML = ''; return; }

  const counts = { leading: 0, weakening: 0, lagging: 0, improving: 0 };
  _allTickers.forEach(t => { if (counts[t.quadrant] != null) counts[t.quadrant]++; });

  const Q_LABELS = { leading: 'Leading', weakening: 'Weakening', lagging: 'Lagging', improving: 'Improving' };
  const Q_ORDER  = ['leading', 'improving', 'weakening', 'lagging'];

  const segments = Q_ORDER
    .filter(q => counts[q] > 0)
    .map(q => {
      const pct = Math.round((counts[q] / total) * 100);
      return `<div class="rrg-summary-seg rrg-summary-seg--${q}" style="flex:${counts[q]}"
        title="${Q_LABELS[q]}: ${counts[q]} ticker (${pct}%)" data-rrg-q="${q}">
        <span class="rrg-summary-seg-label">${counts[q]}</span>
      </div>`;
    }).join('');

  const pills = Q_ORDER
    .filter(q => counts[q] > 0)
    .map(q => {
      const pct = Math.round((counts[q] / total) * 100);
      return `<span class="rrg-summary-pill rrg-q--${q}">${Q_LABELS[q]} <b>${pct}%</b></span>`;
    }).join('');

  bar.innerHTML = `
    <div class="rrg-summary-track">${segments}</div>
    <div class="rrg-summary-pills">${pills}</div>
  `;
}

// ─────────────────────────────────────────────────────────────────────────────
// C8: Filter bar
//
// INVARIANT: All events are delegated to `bar` (the container div).
// bar.innerHTML is rebuilt on every loadRRG() — only bar's children are
// replaced; bar itself persists. Delegating to bar means listeners survive
// across HTML rebuilds with a single wire-once guard.
// ─────────────────────────────────────────────────────────────────────────────

function _renderFilterBar(wrap) {
  let bar = wrap.querySelector('.rrg-filter-bar');

  if (!bar) {
    bar = document.createElement('div');
    bar.className = 'rrg-filter-bar';
    // Position: after summary bar, before canvas
    const summary = wrap.querySelector('.rrg-summary-bar');
    const canvas  = wrap.querySelector(`#${CANVAS_ID}, .rrg-canvas`);
    const anchor  = canvas || wrap.querySelector('.rrg-legend') || null;
    if (summary?.nextSibling) wrap.insertBefore(bar, summary.nextSibling);
    else if (anchor)          wrap.insertBefore(bar, anchor);
    else                      wrap.appendChild(bar);
  }

  // Always rebuild inner HTML to reflect current _allTickers / _extraTickers
  bar.innerHTML = `
    <div class="rrg-filter-controls">
      ${LOOKBACK_OPTIONS.map(w =>
        `<button class="rrg-bulk-btn rrg-lookback-btn" data-rrg-lookback="${w}" type="button">${w}W</button>`
      ).join('')}
      <span class="rrg-filter-divider"></span>
      <button class="rrg-bulk-btn" data-rrg-bulk="all"  type="button">Tất cả</button>
      <button class="rrg-bulk-btn" data-rrg-bulk="none" type="button">Bỏ hết</button>
    </div>
    <div class="rrg-chip-scroll" role="group" aria-label="Bộ lọc ticker RRG">
      ${_allTickers.map((t, idx) => {
        const color   = TRAIL_PALETTE[idx % TRAIL_PALETTE.length];
        const isExtra = _extraTickers.has(t.ticker);
        const qLabel  = t.quadrant ?? '';
        return `<button
          class="rrg-chip${isExtra ? ' rrg-chip--extra' : ''}"
          data-rrg-ticker="${_esc(t.ticker)}"
          style="--chip-color:${color}"
          type="button"
          tabindex="0"
          aria-pressed="true"
          title="${_esc(t.ticker)} — ${_esc(qLabel)}${isExtra ? ' (thêm thủ công)' : ''}"
        >${_esc(t.ticker)}${isExtra
          ? `<span class="rrg-chip-remove" data-rrg-remove="${_esc(t.ticker)}" aria-label="Xóa ${_esc(t.ticker)}">×</span>`
          : ''
        }</button>`;
      }).join('')}
      <form class="rrg-add-form" data-rrg-add-form autocomplete="off">
        <input
          class="rrg-add-input"
          type="text"
          placeholder="+ Mã CP"
          maxlength="10"
          autocomplete="off"
          spellcheck="false"
          data-rrg-add-input
          aria-label="Thêm mã cổ phiếu vào RRG"
        />
      </form>
    </div>
  `;

  // Wire once — guard with dataset flag on bar itself
  if (bar.dataset.rrgBarWired) return;
  bar.dataset.rrgBarWired = '1';

  // ── Click: lookback / bulk / chip-remove / chip-toggle ───────────────────
  bar.addEventListener('click', e => {
    // 1. Lookback button (26W / 52W)
    const lookbackBtn = e.target.closest('[data-rrg-lookback]');
    if (lookbackBtn) {
      const weeks = parseInt(lookbackBtn.dataset.rrgLookback, 10);
      if (weeks !== _lookbackWeeks) {
        _lookbackWeeks = weeks;
        _saveLookback();
        _syncLookbackBtns(bar);
        loadRRG();
      }
      return;
    }

    // 2. Bulk select / deselect
    const bulk = e.target.closest('[data-rrg-bulk]');
    if (bulk) {
      const mode = bulk.dataset.rrgBulk;
      if (mode === 'all')  _hidden = new Set();
      if (mode === 'none') _hidden = new Set(_allTickers.map(t => t.ticker));
      _saveHidden();
      _syncChips(bar);
      _redraw(document.getElementById(WRAP_ID));
      return;
    }

    // 3. Remove extra ticker (× button inside chip)
    const removeBtn = e.target.closest('[data-rrg-remove]');
    if (removeBtn) {
      e.stopPropagation();
      _extraTickers.delete(removeBtn.dataset.rrgRemove);
      _saveExtra();
      loadRRG();
      return;
    }

    // 4. Chip toggle (visibility)
    const chip = e.target.closest('[data-rrg-ticker]');
    if (!chip) return;
    const ticker = chip.dataset.rrgTicker;
    if (_hidden.has(ticker)) {
      _hidden.delete(ticker);
    } else {
      _hidden.add(ticker);
      // Prevent hiding all tickers
      if (_hidden.size >= _allTickers.length) _hidden.delete(ticker);
    }
    _saveHidden();
    _syncChips(bar);
    _redraw(document.getElementById(WRAP_ID));
  });

  // ── Keydown: ←→ chip navigation + Space/Enter activate ───────────────────
  bar.addEventListener('keydown', e => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      const chips = [...bar.querySelectorAll('.rrg-chip')];
      if (!chips.length) return;
      e.preventDefault();
      const cur  = chips.findIndex(c => c === document.activeElement);
      const next = e.key === 'ArrowRight'
        ? Math.min(chips.length - 1, cur + 1)
        : Math.max(0, cur - 1);
      chips[next]?.focus();
      _focusedIdx = next;
      return;
    }
    if (e.key === ' ' || e.key === 'Enter') {
      const chip = e.target.closest('.rrg-chip');
      if (!chip) return;
      e.preventDefault();
      chip.click();
    }
  });

  // ── Submit: add ticker form ───────────────────────────────────────────────
  bar.addEventListener('submit', e => {
    if (!e.target.closest('[data-rrg-add-form]')) return;
    e.preventDefault();
    const input = bar.querySelector('[data-rrg-add-input]');
    if (!input) return;
    const sym = input.value.trim().toUpperCase().replace(/[^A-Z0-9]/g, '');
    input.value = '';
    if (!sym || _extraTickers.size >= MAX_EXTRA) return;
    _extraTickers.add(sym);
    _saveExtra();
    loadRRG();
  });
}

function _syncLookbackBtns(bar) {
  bar.querySelectorAll('[data-rrg-lookback]').forEach(btn => {
    const active = parseInt(btn.dataset.rrgLookback, 10) === _lookbackWeeks;
    btn.classList.toggle('rrg-bulk-btn--active', active);
    btn.setAttribute('aria-pressed', String(active));
  });
}

function _syncChips(bar) {
  bar.querySelectorAll('[data-rrg-ticker]').forEach(chip => {
    const hidden = _hidden.has(chip.dataset.rrgTicker);
    chip.setAttribute('aria-pressed', String(!hidden));
    chip.classList.toggle('rrg-chip--off', hidden);
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// C1: ResizeObserver
// ─────────────────────────────────────────────────────────────────────────────

function _wireResizeObserver(wrap) {
  if (_resizeObs) return;
  if (!window.ResizeObserver) return;
  _resizeObs = new ResizeObserver(() => {
    if (!_allTickers.length) return;
    const visible = _allTickers.filter(t => !_hidden.has(t.ticker));
    _drawCanvas(wrap, visible);
    _renderLegend(wrap, visible);
    _setCanvasA11y();
  });
  _resizeObs.observe(wrap);
}

// ─────────────────────────────────────────────────────────────────────────────
// C3: Hover tooltip
// ─────────────────────────────────────────────────────────────────────────────

function _ensureTooltip(wrap) {
  if (wrap.dataset.rrgTooltipCreated) return;
  const tip = document.createElement('div');
  tip.id        = TOOLTIP_ID;
  tip.className = 'rrg-tooltip rrg-tooltip--hidden';
  tip.setAttribute('aria-hidden', 'true');
  tip.setAttribute('role', 'tooltip');
  wrap.appendChild(tip);
  wrap.dataset.rrgTooltipCreated = '1';
}

function _showTooltip(wrap, ticker, x, y) {
  const tip = wrap.querySelector(`#${TOOLTIP_ID}`);
  if (!tip) return;
  const t     = _allTickers.find(t => t.ticker === ticker);
  if (!t) return;
  const idx   = _allTickers.findIndex(t => t.ticker === ticker);
  const color = TRAIL_PALETTE[idx % TRAIL_PALETTE.length];
  const vel   = _velocity(t);
  const velHtml = vel
    ? `<span class="rrg-tt-vel rrg-tt-vel--${vel.dir}">${vel.icon} ${vel.label}</span>`
    : '';

  tip.innerHTML = `
    <span class="rrg-tt-ticker" style="color:${color}">${_esc(ticker)}</span>
    <span class="rrg-tt-badge rrg-q--${_esc(t.quadrant)}">${_esc(t.quadrant)}</span>
    ${velHtml}
    <span class="rrg-tt-hint">Click để phân tích AI</span>
  `;
  tip.classList.remove('rrg-tooltip--hidden');

  const wW = wrap.offsetWidth;
  const wH = wrap.offsetHeight;
  const tW = 160;
  let left = x + 14;
  let top  = y - 10;
  if (left + tW > wW - 4) left = x - tW - 14;
  if (top  + 80 > wH - 4) top  = y - 80;
  if (top < 4) top = 4;
  tip.style.left = left + 'px';
  tip.style.top  = top  + 'px';
}

function _hideTooltip(wrap) {
  const tip = wrap.querySelector(`#${TOOLTIP_ID}`);
  if (tip) tip.classList.add('rrg-tooltip--hidden');
}

// ─────────────────────────────────────────────────────────────────────────────
// C6: Sidebar detail panel
//
// INVARIANT: _showDetailLoading sets _activeDetail = ticker.
// _renderDetailSignal / _renderDetailError check ticker === _activeDetail
// before rendering — stale async responses are silently discarded.
//
// Close button is delegated to panel (not per-render addEventListener)
// to avoid accumulating listeners across innerHTML rebuilds.
// ─────────────────────────────────────────────────────────────────────────────

function _ensureDetailPanel(wrap) {
  if (document.getElementById(DETAIL_ID)) return;
  const panel = document.createElement('div');
  panel.id        = DETAIL_ID;
  panel.className = 'rrg-detail rrg-detail--hidden';
  panel.setAttribute('aria-live', 'polite');
  wrap.appendChild(panel);

  // Wire close button once via delegation — survives innerHTML rebuilds
  panel.addEventListener('click', e => {
    if (e.target.closest('.rrg-detail-close')) {
      panel.classList.add('rrg-detail--hidden');
      _activeDetail = null;
    }
    if (e.target.closest('.rrg-detail-thesis-btn')) {
      const tid = e.target.closest('.rrg-detail-thesis-btn').dataset.thesisId;
      if (!tid) return;
      loadThesisDetail(tid).then(() => {
        document.getElementById('thesisDetail')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }).catch(() => {});
    }
  });
}

function _showDetailLoading(ticker) {
  const panel = document.getElementById(DETAIL_ID);
  if (!panel) return;
  const idx   = _allTickers.findIndex(t => t.ticker === ticker);
  const color = TRAIL_PALETTE[idx % TRAIL_PALETTE.length];
  const t     = _allTickers.find(t => t.ticker === ticker);
  const vel   = t ? _velocity(t) : null;

  panel.innerHTML = `
    <div class="rrg-detail-header">
      <div class="rrg-detail-title-row">
        <span class="rrg-detail-ticker" style="color:${color}">${_esc(ticker)}</span>
        ${t   ? `<span class="rrg-badge rrg-q--${_esc(t.quadrant)}">${_esc(t.quadrant)}</span>` : ''}
        ${vel ? `<span class="rrg-detail-vel rrg-tt-vel--${vel.dir}">${vel.icon} ${vel.label}</span>` : ''}
      </div>
      <button class="rrg-detail-close" type="button" aria-label="Đóng">✕</button>
    </div>
    <div class="rrg-detail-body">
      <div class="rrg-detail-coords">
        ${t ? `<span>RS-Ratio <b>${t.rs_ratio?.toFixed(2) ?? '—'}</b></span><span>RS-Mom <b>${t.rs_momentum?.toFixed(2) ?? '—'}</b></span>` : ''}
      </div>
      <p class="rrg-detail-loading">
        <span class="rrg-detail-spinner"></span> Đang phân tích AI…
      </p>
    </div>
  `;
  panel.classList.remove('rrg-detail--hidden');
  _activeDetail = ticker;
}

function _renderDetailSignal(ticker, d) {
  if (ticker !== _activeDetail) return; // stale — discard
  const panel = document.getElementById(DETAIL_ID);
  if (!panel) return;

  const idx     = _allTickers.findIndex(t => t.ticker === ticker);
  const color   = TRAIL_PALETTE[idx % TRAIL_PALETTE.length];
  const t       = _allTickers.find(t => t.ticker === ticker);
  const vel     = t ? _velocity(t) : null;
  const confPct = Math.round((d.confidence ?? 0) * 100);
  const SIGNAL_ICONS  = { BUY: '▲', WATCH: '◉', HOLD: '─', REDUCE: '▽', AVOID: '✕' };
  const PATTERN_LABELS = {
    ENTERING_LEADING: 'Đang vào Leading', EXITING_LEADING: 'Rời Leading',
    ENTERING_IMPROVING: 'Đang vào Improving', DEEP_LAGGING: 'Lagging sâu',
    WEAKENING_FAST: 'Suy yếu nhanh', RECOVERY: 'Đang phục hồi',
    ROTATING: 'Đang luân chuyển', STABLE: 'Ổn định',
  };
  const icon    = SIGNAL_ICONS[d.signal]    ?? '?';
  const pattern = PATTERN_LABELS[d.pattern] ?? d.pattern ?? '';

  const thesisLink = d.thesis_id
    ? `<button class="rrg-detail-thesis-btn" data-thesis-id="${_esc(String(d.thesis_id))}" type="button">Xem Thesis →</button>`
    : '';

  panel.innerHTML = `
    <div class="rrg-detail-header">
      <div class="rrg-detail-title-row">
        <span class="rrg-detail-ticker" style="color:${color}">${_esc(ticker)}</span>
        ${t   ? `<span class="rrg-badge rrg-q--${_esc(t.quadrant)}">${_esc(t.quadrant)}</span>` : ''}
        ${vel ? `<span class="rrg-detail-vel rrg-tt-vel--${vel.dir}">${vel.icon} ${vel.label}</span>` : ''}
      </div>
      <button class="rrg-detail-close" type="button" aria-label="Đóng">✕</button>
    </div>
    <div class="rrg-detail-body">
      ${t ? `<div class="rrg-detail-coords">
        <span>RS-Ratio <b>${t.rs_ratio?.toFixed(2) ?? '—'}</b></span>
        <span>RS-Mom <b>${t.rs_momentum?.toFixed(2) ?? '—'}</b></span>
      </div>` : ''}
      <div class="rrg-detail-signal-row">
        <span class="rrg-popup-signal rrg-signal--${_esc(d.signal)}">${icon} ${_esc(d.signal)}</span>
        <span class="rrg-popup-pattern">${_esc(pattern)}</span>
      </div>
      ${d.signal_reason ? `<div class="rrg-detail-row"><span class="rrg-detail-label">Lý do</span><span class="rrg-detail-val">${_esc(d.signal_reason)}</span></div>` : ''}
      ${d.opportunity   ? `<div class="rrg-detail-row"><span class="rrg-detail-label">Cơ hội</span><span class="rrg-detail-val">${_esc(d.opportunity)}</span></div>`   : ''}
      ${d.risk          ? `<div class="rrg-detail-row"><span class="rrg-detail-label">Rủi ro</span><span class="rrg-detail-val">${_esc(d.risk)}</span></div>`          : ''}
      ${d.next_watch    ? `<div class="rrg-detail-row"><span class="rrg-detail-label">Theo dõi tiếp</span><span class="rrg-detail-val">${_esc(d.next_watch)}</span></div>` : ''}
      <div class="rrg-detail-conf">
        <div class="rrg-popup-conf-bar"><div class="rrg-popup-conf-fill" style="width:${confPct}%"></div></div>
        <span class="rrg-popup-conf-label">Confidence ${confPct}%</span>
      </div>
      ${d.company_name ? `<p class="rrg-detail-company">${_esc(d.company_name)}</p>` : ''}
      ${d.sector       ? `<p class="rrg-detail-sector">${_esc(d.sector)}</p>`       : ''}
      ${thesisLink}
    </div>
  `;
}

function _renderDetailError(ticker, msg) {
  if (ticker !== _activeDetail) return; // stale — discard
  const panel = document.getElementById(DETAIL_ID);
  if (!panel) return;

  const idx   = _allTickers.findIndex(t => t.ticker === ticker);
  const color = TRAIL_PALETTE[idx % TRAIL_PALETTE.length];

  panel.innerHTML = `
    <div class="rrg-detail-header">
      <span class="rrg-detail-ticker" style="color:${color}">${_esc(ticker)}</span>
      <button class="rrg-detail-close" type="button" aria-label="Đóng">✕</button>
    </div>
    <div class="rrg-detail-body">
      <p style="color:var(--danger);font-size:var(--text-xs)">${_esc(msg)}</p>
    </div>
  `;
}

// ─────────────────────────────────────────────────────────────────────────────
// Canvas events: click + hover + keyboard
//
// INVARIANT: All listeners are delegated to `wrap` (wire-once guard).
// Canvas is created lazily inside _drawCanvas — always looked up dynamically.
// ─────────────────────────────────────────────────────────────────────────────

function _wireWrapEvents(wrap) {
  if (wrap.dataset.rrgWrapWired) return;
  wrap.dataset.rrgWrapWired = '1';

  // ── Click: canvas hit-test → detail panel; outside → close panel ─────────
  wrap.addEventListener('click', e => {
    const onCanvas = e.target.closest(`#${CANVAS_ID}`);
    const onDetail = e.target.closest(`#${DETAIL_ID}`);

    if (onDetail) return; // let panel's own delegated handler manage it

    if (!onCanvas) {
      // Click outside canvas and outside detail → close panel
      const panel = document.getElementById(DETAIL_ID);
      if (panel && !panel.classList.contains('rrg-detail--hidden')) {
        panel.classList.add('rrg-detail--hidden');
        _activeDetail = null;
      }
      return;
    }

    const cv   = document.getElementById(CANVAS_ID);
    const rect = cv.getBoundingClientRect();
    const cx   = e.clientX - rect.left;
    const cy   = e.clientY - rect.top;
    const ticker = _hitTest(cx, cy);

    if (ticker) {
      _showDetailLoading(ticker);
      _fetchRotation(ticker);
    } else {
      const panel = document.getElementById(DETAIL_ID);
      if (panel) { panel.classList.add('rrg-detail--hidden'); _activeDetail = null; }
    }
  });

  // ── Mousemove: hover tooltip ──────────────────────────────────────────────
  wrap.addEventListener('mousemove', e => {
    const cv = document.getElementById(CANVAS_ID);
    if (!cv || !e.target.closest(`#${CANVAS_ID}`)) { _hideTooltip(wrap); return; }
    const rect   = cv.getBoundingClientRect();
    const cx     = e.clientX - rect.left;
    const cy     = e.clientY - rect.top;
    const ticker = _hitTest(cx, cy);
    if (ticker) {
      _showTooltip(wrap, ticker, cx, cy);
      cv.style.cursor = 'pointer';
    } else {
      _hideTooltip(wrap);
      cv.style.cursor = 'default';
    }
  });

  wrap.addEventListener('mouseleave', () => _hideTooltip(wrap));

  // ── Keydown: ←→ on focused canvas ────────────────────────────────────────
  wrap.addEventListener('keydown', e => {
    if (!e.target.matches(`#${CANVAS_ID}`)) return;
    const visible = _allTickers.filter(t => !_hidden.has(t.ticker));
    if (!visible.length) return;

    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault();
      _focusedIdx = e.key === 'ArrowRight'
        ? (_focusedIdx + 1) % visible.length
        : (_focusedIdx - 1 + visible.length) % visible.length;
      const ticker = visible[_focusedIdx]?.ticker;
      if (ticker) {
        _drawCanvas(wrap, visible, ticker);
        _showDetailLoading(ticker);
        _fetchRotation(ticker);
      }
    }

    if (e.key === 'Escape') {
      _focusedIdx = -1;
      _redraw(wrap);
      const panel = document.getElementById(DETAIL_ID);
      if (panel) { panel.classList.add('rrg-detail--hidden'); _activeDetail = null; }
    }
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Hit test
// ─────────────────────────────────────────────────────────────────────────────

function _hitTest(cx, cy) {
  let best = null;
  let bestDist = HIT_RADIUS;
  _headPositions.forEach((pos, ticker) => {
    const d = Math.hypot(pos.x - cx, pos.y - cy);
    if (d < bestDist) { bestDist = d; best = ticker; }
  });
  return best;
}

// ─────────────────────────────────────────────────────────────────────────────
// Canvas draw
// ─────────────────────────────────────────────────────────────────────────────

function _drawCanvas(wrap, tickers, focusedTicker = null) {
  let cv = document.getElementById(CANVAS_ID);
  if (!cv) {
    cv = document.createElement('canvas');
    cv.id        = CANVAS_ID;
    cv.className = 'rrg-canvas';
    const bar = wrap.querySelector('.rrg-filter-bar');
    if (bar) bar.after(cv);
    else wrap.appendChild(cv);
  }

  const dpr  = window.devicePixelRatio || 1;
  const size = wrap.clientWidth || 320;
  cv.width        = size * dpr;
  cv.height       = size * dpr;
  cv.style.width  = size + 'px';
  cv.style.height = size + 'px';

  const ctx = cv.getContext('2d');
  ctx.scale(dpr, dpr);

  const W   = size;
  const H   = size;
  const PAD = 38;
  const plotW = W - PAD * 2;
  const plotH = H - PAD * 2;

  // Axis range centred at 100 — use ALL tickers (not just visible) to keep scale stable
  const allR = _allTickers.flatMap(t => t.trail.map(p => p.rs_ratio));
  const allM = _allTickers.flatMap(t => t.trail.map(p => p.rs_momentum));
  const MIN_HALF = 3.5;
  const MARGIN   = 1.8;
  const rHalf  = Math.max(MIN_HALF, ...allR.map(v => Math.abs(v - 100))) + MARGIN;
  const mHalf  = Math.max(MIN_HALF, ...allM.map(v => Math.abs(v - 100))) + MARGIN;
  const halfSpan = Math.max(rHalf, mHalf);
  const rMin = 100 - halfSpan, rMax = 100 + halfSpan;
  const mMin = 100 - halfSpan, mMax = 100 + halfSpan;

  const toX = r => PAD + ((r - rMin) / (rMax - rMin)) * plotW;
  const toY = m => PAD + ((mMax - m) / (mMax - mMin)) * plotH;
  const cx  = toX(100);
  const cy  = toY(100);

  ctx.clearRect(0, 0, W, H);

  // Quadrant backgrounds
  const quads = [
    { key: 'leading',   x: cx,  y: PAD, w: W - cx - PAD, h: cy - PAD     },
    { key: 'weakening', x: cx,  y: cy,  w: W - cx - PAD, h: H - cy - PAD },
    { key: 'lagging',   x: PAD, y: cy,  w: cx - PAD,     h: H - cy - PAD },
    { key: 'improving', x: PAD, y: PAD, w: cx - PAD,     h: cy - PAD     },
  ];
  quads.forEach(q => {
    ctx.fillStyle = Q_COLORS[q.key].bg;
    ctx.fillRect(q.x, q.y, q.w, q.h);
  });

  // Minor grid
  ctx.strokeStyle = 'rgba(148,163,184,0.12)';
  ctx.lineWidth   = 0.5;
  ctx.setLineDash([2, 4]);
  const gridStep = halfSpan > 8 ? 5 : 2;
  for (let v = Math.ceil((100 - halfSpan) / gridStep) * gridStep; v <= 100 + halfSpan; v += gridStep) {
    if (v === 100) continue;
    const gx = toX(v); const gy = toY(v);
    ctx.beginPath(); ctx.moveTo(gx, PAD); ctx.lineTo(gx, H - PAD); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(PAD, gy); ctx.lineTo(W - PAD, gy); ctx.stroke();
  }
  ctx.setLineDash([]);

  // Centre axes
  ctx.strokeStyle = 'rgba(148,163,184,0.35)';
  ctx.lineWidth   = 1;
  ctx.beginPath(); ctx.moveTo(cx, PAD);     ctx.lineTo(cx, H - PAD); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(PAD, cy);     ctx.lineTo(W - PAD, cy); ctx.stroke();

  // Quadrant labels
  ctx.font = '600 9px system-ui, sans-serif';
  ctx.textAlign = 'center';
  const QLABEL = { leading:'Leading', weakening:'Weakening', lagging:'Lagging', improving:'Improving' };
  quads.forEach(q => {
    ctx.fillStyle = Q_COLORS[q.key].label;
    ctx.fillText(QLABEL[q.key], q.x + q.w / 2, q.y + q.h / 2 + 3);
  });

  // Axis tick values
  ctx.fillStyle = 'rgba(148,163,184,0.45)';
  ctx.font      = '8px system-ui, sans-serif';
  ctx.textAlign = 'center';
  for (let v = Math.ceil((100 - halfSpan) / gridStep) * gridStep; v <= 100 + halfSpan; v += gridStep * 2) {
    if (Math.abs(v - 100) < 0.5) continue;
    ctx.fillText(v, toX(v), H - PAD + 12);
  }
  ctx.textAlign = 'right';
  for (let v = Math.ceil((100 - halfSpan) / gridStep) * gridStep; v <= 100 + halfSpan; v += gridStep * 2) {
    if (Math.abs(v - 100) < 0.5) continue;
    ctx.fillText(v, PAD - 4, toY(v) + 3);
  }

  // Axis labels
  ctx.fillStyle = 'rgba(148,163,184,0.55)';
  ctx.font      = '8px system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText('RS-Ratio →', W / 2, H - 6);
  ctx.save();
  ctx.translate(10, H / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText('RS-Momentum ↑', 0, 0);
  ctx.restore();

  // Trails — unfocused first (dimmed), focused on top
  const hasFocus = Boolean(focusedTicker);
  tickers.forEach(t => { if (hasFocus && t.ticker !== focusedTicker) _drawTicker(ctx, t, toX, toY, 0.22); });
  tickers.forEach(t => { if (!hasFocus || t.ticker === focusedTicker) _drawTicker(ctx, t, toX, toY, 1.0); });

  // Head positions for hit-test
  _headPositions = new Map();
  tickers.forEach(t => {
    const trail = t.trail;
    if (!trail.length) return;
    const head = trail[trail.length - 1];
    _headPositions.set(t.ticker, { x: toX(head.rs_ratio), y: toY(head.rs_momentum) });
  });

  // Date stamp
  if (_asOf) {
    ctx.font      = '8px system-ui, sans-serif';
    ctx.fillStyle = 'rgba(97,113,143,0.65)';
    ctx.textAlign = 'right';
    ctx.fillText(`as of ${_asOf}`, W - PAD, H - 6);
  }
}

function _drawTicker(ctx, ticker, toX, toY, masterAlpha) {
  const idx   = _allTickers.findIndex(t => t.ticker === ticker.ticker);
  const color = TRAIL_PALETTE[idx % TRAIL_PALETTE.length];
  const trail = ticker.trail;
  const n     = trail.length;

  // Trail segments — fade oldest → newest
  for (let i = 1; i < n; i++) {
    const progress = i / (n - 1);
    const segAlpha = (0.12 + progress * 0.78) * masterAlpha;
    const segWidth = 0.6 + progress * 2.4;
    const alphaHex = Math.round(segAlpha * 255).toString(16).padStart(2, '0');
    const x0 = toX(trail[i-1].rs_ratio); const y0 = toY(trail[i-1].rs_momentum);
    const x1 = toX(trail[i].rs_ratio);   const y1 = toY(trail[i].rs_momentum);
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.lineTo(x1, y1);
    ctx.strokeStyle = color + alphaHex;
    ctx.lineWidth   = segWidth;
    ctx.stroke();
  }

  // Trail dots
  trail.forEach((pt, i) => {
    const x        = toX(pt.rs_ratio);
    const y        = toY(pt.rs_momentum);
    const isHead   = i === n - 1;
    const progress = n > 1 ? i / (n - 1) : 1;
    const alpha    = (isHead ? 1.0 : 0.10 + progress * 0.55) * masterAlpha;
    const radius   = isHead ? 5 : 1.5 + progress * 1.2;
    const alphaHex = Math.round(alpha * 255).toString(16).padStart(2, '0');

    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fillStyle = isHead ? color : color + alphaHex;
    ctx.fill();

    if (isHead) {
      ctx.beginPath();
      ctx.arc(x, y, radius + 2.5, 0, Math.PI * 2);
      ctx.strokeStyle = color + Math.round(0.35 * masterAlpha * 255).toString(16).padStart(2, '0');
      ctx.lineWidth   = 1.2;
      ctx.stroke();
    }
  });

  // C2: Velocity arrow at head
  if (n >= 2) {
    const prev   = trail[n - 2];
    const head   = trail[n - 1];
    const dx     = toX(head.rs_ratio)    - toX(prev.rs_ratio);
    const dy     = toY(head.rs_momentum) - toY(prev.rs_momentum);
    const len    = Math.hypot(dx, dy);
    if (len > 2) {
      const ux     = dx / len;
      const uy     = dy / len;
      const hx     = toX(head.rs_ratio);
      const hy     = toY(head.rs_momentum);
      const arrLen = Math.min(14, len * 0.6 + 6);
      const ax     = hx + ux * arrLen;
      const ay     = hy + uy * arrLen;
      const aHex   = Math.round(0.70 * masterAlpha * 255).toString(16).padStart(2, '0');

      ctx.beginPath();
      ctx.moveTo(hx + ux * 6, hy + uy * 6);
      ctx.lineTo(ax, ay);
      ctx.strokeStyle = color + aHex;
      ctx.lineWidth   = 1.5;
      ctx.stroke();

      const angle = Math.atan2(uy, ux);
      const aSize = 4;
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(ax - aSize * Math.cos(angle - 0.45), ay - aSize * Math.sin(angle - 0.45));
      ctx.lineTo(ax - aSize * Math.cos(angle + 0.45), ay - aSize * Math.sin(angle + 0.45));
      ctx.closePath();
      ctx.fillStyle = color + aHex;
      ctx.fill();
    }
  }

  // C2: Label at head — clamped inside canvas bounds
  const head    = trail[n - 1];
  const hx      = toX(head.rs_ratio);
  const hy      = toY(head.rs_momentum);
  const canvasW = ctx.canvas.width / (window.devicePixelRatio || 1);
  const canvasH = ctx.canvas.height / (window.devicePixelRatio || 1);
  const PAD_EDGE = 4;

  ctx.font = 'bold 9px system-ui, sans-serif';
  const lw = ctx.measureText(ticker.ticker).width + 6;
  const lh = 13;
  let lx = hx + 7;
  let ly = hy - 5;

  if (lx + lw > canvasW - PAD_EDGE) lx = hx - lw - 7;
  if (lx < PAD_EDGE) lx = PAD_EDGE;
  if (ly - lh < PAD_EDGE) ly = hy + lh + 2;
  if (ly > canvasH - PAD_EDGE) ly = hy - 5;

  ctx.fillStyle = 'rgba(8,17,31,0.75)';
  ctx.fillRect(lx - 2, ly - lh + 2, lw, lh);
  ctx.fillStyle = color;
  ctx.textAlign = 'left';
  ctx.fillText(ticker.ticker, lx, ly);
}

// ─────────────────────────────────────────────────────────────────────────────
// C2: Velocity
// ─────────────────────────────────────────────────────────────────────────────

function _velocity(t) {
  const trail = t.trail;
  if (!trail || trail.length < 2) return null;
  const prev = trail[trail.length - 2];
  const head = trail[trail.length - 1];
  const dr   = head.rs_ratio    - prev.rs_ratio;
  const dm   = head.rs_momentum - prev.rs_momentum;
  const mag  = Math.hypot(dr, dm);
  if (mag < 0.05) return { dir: 'neutral', icon: '→', label: 'Ổn định' };
  if (dm >  0.1 && dr >  0.1) return { dir: 'up',   icon: '↗', label: 'Tăng tốc' };
  if (dm >  0.1 && dr < -0.1) return { dir: 'up',   icon: '↖', label: 'Momentum tăng' };
  if (dm < -0.1 && dr >  0.1) return { dir: 'down', icon: '↘', label: 'Yếu dần' };
  if (dm < -0.1 && dr < -0.1) return { dir: 'down', icon: '↙', label: 'Suy yếu' };
  if (dm >  0.1) return { dir: 'up',   icon: '↑', label: 'Momentum tăng' };
  if (dm < -0.1) return { dir: 'down', icon: '↓', label: 'Momentum giảm' };
  if (dr >  0.1) return { dir: 'up',   icon: '→', label: 'Ratio tăng' };
  return               { dir: 'down',  icon: '←', label: 'Ratio giảm' };
}

// ─────────────────────────────────────────────────────────────────────────────
// C7: Legend with sparklines
//
// INVARIANT: click is delegated to the legend container — not per-item.
// legend.innerHTML is rebuilt on every call; per-item listeners would
// accumulate across calls.
// ─────────────────────────────────────────────────────────────────────────────

function _renderLegend(wrap, visibleTickers) {
  let legend = wrap.querySelector('.rrg-legend');
  if (!legend) {
    legend = document.createElement('div');
    legend.className = 'rrg-legend';
    wrap.appendChild(legend);

    // Wire once — delegate to legend container
    legend.addEventListener('click', e => {
      const btn = e.target.closest('[data-rrg-legend-ticker]');
      if (!btn) return;
      const ticker = btn.dataset.rrgLegendTicker;
      if (!ticker) return;
      _showDetailLoading(ticker);
      _fetchRotation(ticker);
    });
  }

  if (!visibleTickers.length) {
    legend.innerHTML = '<span class="rrg-legend-empty">Không có ticker nào được chọn</span>';
    return;
  }

  legend.innerHTML = visibleTickers.map(t => {
    const idx     = _allTickers.findIndex(x => x.ticker === t.ticker);
    const color   = TRAIL_PALETTE[idx % TRAIL_PALETTE.length];
    const vel     = _velocity(t);
    const velSpan = vel
      ? `<span class="rrg-legend-vel rrg-tt-vel--${vel.dir}" title="${vel.label}">${vel.icon}</span>`
      : '';
    return `<button
      class="rrg-legend-item"
      type="button"
      data-rrg-legend-ticker="${_esc(t.ticker)}"
      title="${_esc(t.ticker)} — ${_esc(t.quadrant)}. Click để phân tích AI"
      aria-label="${_esc(t.ticker)}"
    >${_sparklineSVG(t, color)}
      <span class="rrg-ticker-label" style="color:${color}">${_esc(t.ticker)}</span>
      <span class="rrg-badge rrg-q--${t.quadrant}">${_esc(t.quadrant)}</span>
      ${velSpan}
    </button>`;
  }).join('');
}

function _sparklineSVG(t, color) {
  const trail = t.trail;
  if (!trail || trail.length < 2) {
    return `<svg class="rrg-sparkline" viewBox="0 0 32 20" aria-hidden="true">
      <line x1="2" y1="10" x2="30" y2="10" stroke="${color}" stroke-width="1" stroke-opacity="0.3"/>
    </svg>`;
  }

  const rs  = trail.map(p => p.rs_ratio);
  const mom = trail.map(p => p.rs_momentum);
  const rMin = Math.min(...rs);  const rMax = Math.max(...rs);
  const mMin = Math.min(...mom); const mMax = Math.max(...mom);
  const rRange = rMax - rMin || 1;
  const mRange = mMax - mMin || 1;

  const pts = trail.map(p => {
    const x = 2 + ((p.rs_ratio - rMin) / rRange) * 28;
    const y = 18 - ((p.rs_momentum - mMin) / mRange) * 16;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const head  = trail[trail.length - 1];
  const headX = 2 + ((head.rs_ratio - rMin) / rRange) * 28;
  const headY = 18 - ((head.rs_momentum - mMin) / mRange) * 16;

  return `<svg class="rrg-sparkline" viewBox="0 0 32 20" aria-hidden="true">
    <polyline points="${pts.join(' ')}" fill="none" stroke="${color}"
      stroke-width="1.2" stroke-opacity="0.7" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${headX.toFixed(1)}" cy="${headY.toFixed(1)}" r="2" fill="${color}"/>
  </svg>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// AI rotation signal fetch
// ─────────────────────────────────────────────────────────────────────────────

async function _fetchRotation(ticker) {
  try {
    const data = await getJson(
      `${ROTATION_API}/${encodeURIComponent(ticker)}?lookback_weeks=${_lookbackWeeks}`
    );
    if (data?.error) _renderDetailError(ticker, data.error);
    else             _renderDetailSignal(ticker, data);
  } catch (err) {
    _renderDetailError(ticker, err.message);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Status helpers
// ─────────────────────────────────────────────────────────────────────────────

function _setStatus(msg) {
  const el = document.getElementById(STATUS_ID);
  if (el) el.textContent = msg;
}
function _clearStatus() {
  const el = document.getElementById(STATUS_ID);
  if (el) el.textContent = '';
}
function _esc(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
