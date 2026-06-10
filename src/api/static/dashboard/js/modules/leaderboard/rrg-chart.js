/**
 * rrg-chart.js — RRG (Relative Rotation Graph) Canvas renderer
 * Owner  : dashboard / market adapter
 * API    : GET /api/v1/rrg/thesis
 * HTML   : #rrgWrap  →  <canvas id="rrgCanvas">
 * CSS    : css/modules/leaderboard.css  (.rrg-*)
 *
 * Layout:
 *   - 4 quadrants centred at (100, 100)
 *   - X axis = RS-Ratio   (right = stronger than market)
 *   - Y axis = RS-Momentum (up   = accelerating)
 *   - Each ticker: trail line (faint) + dots + label at head
 *   - Quadrant labels: Leading / Weakening / Lagging / Improving
 *
 * Filter:
 *   - Legend items are toggle-buttons — click to show/hide a ticker
 *   - "All / None" shortcut buttons for bulk toggle
 *   - Filter state is client-side only (no re-fetch)
 *   - Canvas re-renders immediately on every toggle
 *
 * Trail: 8 weekly points, oldest → newest.
 *   Head = newest point (filled circle, larger).
 *   Tail = older points (smaller, fading opacity).
 *
 * Exports:
 *   loadRRG()  — fetch + render; safe to call on every dashboard refresh
 */

import { getJson } from '../../api/client.js';

// ── Config ────────────────────────────────────────────────────────────────
const API_URL         = '/api/v1/rrg/thesis';
const LOOKBACK_OPTIONS = [26, 52];  // weeks — maps to toggle buttons
const LOOKBACK_KEY     = 'rrg_lookback_weeks';
const CANVAS_ID = 'rrgCanvas';
const WRAP_ID   = 'rrgWrap';
const STATUS_ID = 'rrgStatus';

const Q_COLORS = {
  leading:   { bg: 'rgba(74,222,128,0.06)',  label: 'rgba(74,222,128,0.50)'  },
  weakening: { bg: 'rgba(251,191,36,0.06)',  label: 'rgba(251,191,36,0.50)'  },
  lagging:   { bg: 'rgba(248,113,113,0.06)', label: 'rgba(248,113,113,0.50)' },
  improving: { bg: 'rgba(125,211,252,0.06)', label: 'rgba(125,211,252,0.50)' },
};

const TRAIL_PALETTE = [
  '#7dd3fc', '#4ade80', '#fbbf24', '#f87171',
  '#a78bfa', '#fb923c', '#60a5fa', '#34d399',
  '#f472b6', '#e879f9',
];

// ── Module state ──────────────────────────────────────────────────────────
// Cached after first fetch; filter operates on this.
let _allTickers    = [];        // full list from API (valid entries only)
let _hidden        = new Set(); // tickers currently hidden
let _asOf          = null;
let _lookbackWeeks = _loadLookback(); // 26 or 52 — persisted

// ── Persistence ──────────────────────────────────────────────────────────
const _STORAGE_KEY = 'rrg_hidden_tickers';

function _saveHidden() {
  try {
    localStorage.setItem(_STORAGE_KEY, JSON.stringify([..._hidden]));
  } catch (_) { /* storage unavailable — silent */ }
}

/** Persist + restore lookback weeks selection. */
function _saveLookback() {
  try { localStorage.setItem(LOOKBACK_KEY, String(_lookbackWeeks)); } catch (_) {}
}
function _loadLookback() {
  try {
    const v = parseInt(localStorage.getItem(LOOKBACK_KEY), 10);
    return LOOKBACK_OPTIONS.includes(v) ? v : LOOKBACK_OPTIONS[0];
  } catch (_) { return LOOKBACK_OPTIONS[0]; }
}

/** Restore persisted hidden set, clamped to currently valid tickers. */
function _loadHidden(validTickers) {
  try {
    const raw = localStorage.getItem(_STORAGE_KEY);
    if (!raw) return new Set();
    const saved = new Set(JSON.parse(raw));
    // Only keep tickers that still exist in the current fetch
    const clamped = new Set([...saved].filter(t => validTickers.has(t)));
    // Guard: if every current ticker is hidden, fall back to showing all
    if (clamped.size >= validTickers.size) return new Set();
    return clamped;
  } catch (_) {
    return new Set();
  }
}

// ── Public ────────────────────────────────────────────────────────────────

export async function loadRRG() {
  const wrap = document.getElementById(WRAP_ID);
  if (!wrap) return;

  _setStatus('Đang tải RRG…');

  try {
    // trail_points=0 → backend auto-derives from lookback_weeks
    // (26W→13pts, 52W→26pts) so trail length reflects the chosen window.
    const data = await getJson(`${API_URL}?lookback_weeks=${_lookbackWeeks}&trail_points=0`);
    const tickers = data?.tickers ?? [];

    if (!tickers.length) {
      _setStatus('Chưa có thesis active để vẽ RRG.');
      return;
    }

    const valid = tickers.filter(t => t.trail && t.trail.length >= 2);
    if (!valid.length) {
      _setStatus('Dữ liệu giá chưa đủ để tính RRG (cần ≥ 55 phiên).');
      return;
    }

    _allTickers = valid;
    _hidden     = _loadHidden(new Set(valid.map(t => t.ticker)));
    _asOf       = data.as_of ?? null;

    _clearStatus();
    _renderFilterBar(wrap);
    // Sync chip + lookback toggle visual state with restored values
    const bar = wrap.querySelector('.rrg-filter-bar');
    if (bar) { _syncChips(bar); _syncLookbackBtns(bar); }
    _redraw(wrap);
  } catch (err) {
    _setStatus(`Không tải được RRG: ${err.message}`);
  }
}

// ── Filter bar ────────────────────────────────────────────────────────────

function _renderFilterBar(wrap) {
  let bar = wrap.querySelector('.rrg-filter-bar');
  const isNew = !bar;

  if (isNew) {
    bar = document.createElement('div');
    bar.className = 'rrg-filter-bar';
    const firstChild = wrap.querySelector('.rrg-canvas, .rrg-legend');
    if (firstChild) wrap.insertBefore(bar, firstChild);
    else wrap.appendChild(bar);
  }

  // Rebuild chip HTML every time tickers may have changed (new fetch).
  // Event listener is added ONLY on first creation to avoid stacking.
  bar.innerHTML = `
    ${LOOKBACK_OPTIONS.map(w =>
      `<button class="rrg-bulk-btn rrg-lookback-btn" data-rrg-lookback="${w}" type="button">${w}W</button>`
    ).join('')}
    <span class="rrg-filter-divider"></span>
    <button class="rrg-bulk-btn" data-rrg-bulk="all"  type="button">Tất cả</button>
    <button class="rrg-bulk-btn" data-rrg-bulk="none" type="button">Bỏ hết</button>
    <span class="rrg-filter-divider"></span>
    ${_allTickers.map((t, idx) => {
      const color = TRAIL_PALETTE[idx % TRAIL_PALETTE.length];
      return `<button
        class="rrg-chip"
        data-rrg-ticker="${_esc(t.ticker)}"
        style="--chip-color:${color}"
        type="button"
        aria-pressed="true"
        title="${_esc(t.ticker)} — ${_esc(t.quadrant)}"
      >${_esc(t.ticker)}</button>`;
    }).join('')}
  `;

  // Wire click ONCE — guard with dataset flag to survive innerHTML rebuilds
  if (isNew) {
    bar.addEventListener('click', e => {
      // Lookback toggle (26W / 52W) — triggers re-fetch
      const lookbackBtn = e.target.closest('[data-rrg-lookback]');
      if (lookbackBtn) {
        const weeks = parseInt(lookbackBtn.dataset.rrgLookback, 10);
        if (weeks !== _lookbackWeeks) {
          _lookbackWeeks = weeks;
          _saveLookback();
          _syncLookbackBtns(bar);
          loadRRG(); // re-fetch with new lookback
        }
        return;
      }

      const bulk = e.target.closest('[data-rrg-bulk]');
      if (bulk) {
        const mode = bulk.dataset.rrgBulk;
        if (mode === 'all')  _hidden = new Set();
        if (mode === 'none') _hidden = new Set(_allTickers.map(t => t.ticker));
        _saveHidden();
        _syncChips(bar);
        _redraw(wrap);
        return;
      }

      const chip = e.target.closest('[data-rrg-ticker]');
      if (chip) {
        const ticker = chip.dataset.rrgTicker;
        // Toggle: add to hidden if visible, remove if already hidden
        if (_hidden.has(ticker)) {
          _hidden.delete(ticker);
        } else {
          _hidden.add(ticker);
          // Guard: cannot hide all — revert if this was the last visible one
          if (_hidden.size === _allTickers.length) _hidden.delete(ticker);
        }
        _saveHidden();
        _syncChips(bar);
        _redraw(wrap);
      }
    });
  }
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

// ── Re-draw (uses current _hidden state) ─────────────────────────────────

function _redraw(wrap) {
  const visible = _allTickers.filter(t => !_hidden.has(t.ticker));
  _drawCanvas(wrap, visible);
  _renderLegend(wrap, visible);
}

// ── Canvas ────────────────────────────────────────────────────────────────

function _drawCanvas(wrap, tickers) {
  let canvas = document.getElementById(CANVAS_ID);
  if (!canvas) {
    canvas = document.createElement('canvas');
    canvas.id = CANVAS_ID;
    canvas.className = 'rrg-canvas';
    wrap.appendChild(canvas);
  }

  const dpr  = window.devicePixelRatio || 1;
  const size = wrap.clientWidth || 320;
  canvas.width  = size * dpr;
  canvas.height = size * dpr;
  canvas.style.width  = size + 'px';
  canvas.style.height = size + 'px';

  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  const W = size, H = size;
  const PAD = 36;
  const plotW = W - PAD * 2;
  const plotH = H - PAD * 2;

  // ── Axis range — always based on full dataset so axes don't jump when filtering.
  // Centre is ALWAYS fixed at 100 so the 4 quadrants are always equal size.
  // halfSpan = max distance any point has from 100, plus a fixed padding.
  const allR = _allTickers.flatMap(t => t.trail.map(p => p.rs_ratio));
  const allM = _allTickers.flatMap(t => t.trail.map(p => p.rs_momentum));
  const MIN_HALF = 3.5;   // minimum half-span so chart never collapses
  const MARGIN   = 1.5;   // extra breathing room beyond the farthest point

  const rHalf = Math.max(MIN_HALF, ...allR.map(v => Math.abs(v - 100))) + MARGIN;
  const mHalf = Math.max(MIN_HALF, ...allM.map(v => Math.abs(v - 100))) + MARGIN;
  // Use the larger of the two so X and Y scales are equal (square quadrants)
  const halfSpan = Math.max(rHalf, mHalf);

  const finalRMin = 100 - halfSpan;
  const finalRMax = 100 + halfSpan;
  const finalMMin = 100 - halfSpan;
  const finalMMax = 100 + halfSpan;

  const toX = r => PAD + ((r - finalRMin) / (finalRMax - finalRMin)) * plotW;
  const toY = m => PAD + ((finalMMax - m) / (finalMMax - finalMMin)) * plotH;

  const cx = toX(100);
  const cy = toY(100);

  ctx.clearRect(0, 0, W, H);

  // ── Quadrant backgrounds
  const quads = [
    { key: 'leading',   x: cx,  y: PAD, w: W - cx - PAD,  h: cy - PAD     },
    { key: 'weakening', x: cx,  y: cy,  w: W - cx - PAD,  h: H - cy - PAD },
    { key: 'lagging',   x: PAD, y: cy,  w: cx - PAD,      h: H - cy - PAD },
    { key: 'improving', x: PAD, y: PAD, w: cx - PAD,      h: cy - PAD     },
  ];
  quads.forEach(q => {
    ctx.fillStyle = Q_COLORS[q.key].bg;
    ctx.fillRect(q.x, q.y, q.w, q.h);
  });

  // ── Quadrant labels
  ctx.font = '600 9px system-ui, sans-serif';
  ctx.textAlign = 'center';
  const LABEL = { leading:'Leading', weakening:'Weakening', lagging:'Lagging', improving:'Improving' };
  quads.forEach(q => {
    ctx.fillStyle = Q_COLORS[q.key].label;
    ctx.fillText(LABEL[q.key], q.x + q.w / 2, q.y + q.h / 2 + 3);
  });

  // ── Grid lines
  ctx.strokeStyle = 'rgba(148,163,184,0.25)';
  ctx.lineWidth   = 1;
  ctx.setLineDash([3, 3]);
  ctx.beginPath(); ctx.moveTo(cx, PAD);  ctx.lineTo(cx, H - PAD); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(PAD, cy);  ctx.lineTo(W - PAD, cy); ctx.stroke();
  ctx.setLineDash([]);

  // ── Axis labels
  ctx.fillStyle = 'rgba(148,163,184,0.60)';
  ctx.font      = '8px system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText('RS-Ratio →', W / 2, H - 6);
  ctx.save();
  ctx.translate(10, H / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText('RS-Momentum ↑', 0, 0);
  ctx.restore();

  // ── Ticker trails — use stable colour index from _allTickers
  tickers.forEach(ticker => {
    const idx   = _allTickers.findIndex(t => t.ticker === ticker.ticker);
    const color = TRAIL_PALETTE[idx % TRAIL_PALETTE.length];
    const trail = ticker.trail;

    // ── Trail: segment-by-segment so line width + alpha fade oldest→newest
    // t=0 (oldest): thin + faint.  t=n-1 (head): thick + fully opaque.
    const n = trail.length;
    for (let i = 1; i < n; i++) {
      const progress  = i / (n - 1);              // 0 at first segment → 1 at last
      const segAlpha  = 0.12 + progress * 0.78;   // 0.12 → 0.90
      const segWidth  = 0.6  + progress * 2.2;    // 0.6px → 2.8px
      const alphaHex  = Math.round(segAlpha * 255).toString(16).padStart(2, '0');

      const x0 = toX(trail[i - 1].rs_ratio);  const y0 = toY(trail[i - 1].rs_momentum);
      const x1 = toX(trail[i].rs_ratio);      const y1 = toY(trail[i].rs_momentum);

      ctx.beginPath();
      ctx.moveTo(x0, y0);
      ctx.lineTo(x1, y1);
      ctx.strokeStyle = color + alphaHex;
      ctx.lineWidth   = segWidth;
      ctx.stroke();
    }

    // Trail dots — fade oldest, emphasise head
    trail.forEach((pt, i) => {
      const x        = toX(pt.rs_ratio);
      const y        = toY(pt.rs_momentum);
      const isHead   = i === trail.length - 1;
      const progress = n > 1 ? i / (n - 1) : 1;
      const alpha    = isHead ? 1.0 : 0.10 + progress * 0.55;  // 0.10 → 0.65
      const radius   = isHead ? 4.5 : 1.5 + progress * 1.0;    // 1.5px → 2.5px
      const alphaHex = Math.round(alpha * 255).toString(16).padStart(2, '0');

      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fillStyle = isHead ? color : color + alphaHex;
      ctx.fill();

      if (isHead) {
        ctx.beginPath();
        ctx.arc(x, y, radius + 2, 0, Math.PI * 2);
        ctx.strokeStyle = color + '50';
        ctx.lineWidth   = 1;
        ctx.stroke();
      }
    });

    // Ticker label at head
    const head = trail[trail.length - 1];
    const hx   = toX(head.rs_ratio);
    const hy   = toY(head.rs_momentum);
    const lx   = hx + 6;
    const ly   = hy - 4;

    ctx.font      = 'bold 9px system-ui, sans-serif';
    ctx.textAlign = 'left';
    const metrics = ctx.measureText(ticker.ticker);
    ctx.fillStyle = 'rgba(8,17,31,0.72)';
    ctx.fillRect(lx - 2, ly - 9, metrics.width + 6, 13);
    ctx.fillStyle = color;
    ctx.fillText(ticker.ticker, lx, ly);
  });

  // ── Date stamp
  if (_asOf) {
    ctx.font      = '8px system-ui, sans-serif';
    ctx.fillStyle = 'rgba(97,113,143,0.70)';
    ctx.textAlign = 'right';
    ctx.fillText(`as of ${_asOf}`, W - PAD, H - 6);
  }
}

// ── Legend ────────────────────────────────────────────────────────────────

function _renderLegend(wrap, visibleTickers) {
  let legend = wrap.querySelector('.rrg-legend');
  if (!legend) {
    legend = document.createElement('div');
    legend.className = 'rrg-legend';
    wrap.appendChild(legend);
  }

  legend.innerHTML = visibleTickers.map(t => {
    const idx   = _allTickers.findIndex(x => x.ticker === t.ticker);
    const color = TRAIL_PALETTE[idx % TRAIL_PALETTE.length];
    const qCls  = `rrg-q--${t.quadrant}`;
    return `<span class="rrg-legend-item">
      <span class="rrg-dot" style="background:${color}"></span>
      <span class="rrg-ticker-label">${_esc(t.ticker)}</span>
      <span class="rrg-badge ${qCls}">${_esc(t.quadrant)}</span>
    </span>`;
  }).join('') || '<span class="rrg-legend-empty">Không có ticker nào được chọn</span>';
}

// ── Helpers ───────────────────────────────────────────────────────────────

const _minOf = arr => arr.length ? Math.min(...arr) : 100;
const _maxOf = arr => arr.length ? Math.max(...arr) : 100;

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
