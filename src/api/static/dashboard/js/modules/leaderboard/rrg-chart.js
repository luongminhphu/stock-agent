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
 * Trail: 8 weekly points, oldest → newest.
 *   Head = newest point (filled circle, larger).
 *   Tail = older points (smaller, fading opacity).
 *
 * Exports:
 *   loadRRG()  — fetch + render; safe to call on every dashboard refresh
 */

import { getJson } from '../../api/client.js';

// ── Config ────────────────────────────────────────────────────────────────
const API_URL       = '/api/v1/rrg/thesis';
const CANVAS_ID     = 'rrgCanvas';
const WRAP_ID       = 'rrgWrap';
const STATUS_ID     = 'rrgStatus';

// Quadrant colours (match design tokens — used as JS strings for Canvas)
const Q_COLORS = {
  leading:   { bg: 'rgba(74,222,128,0.06)',  label: 'rgba(74,222,128,0.50)'  },
  weakening: { bg: 'rgba(251,191,36,0.06)',  label: 'rgba(251,191,36,0.50)'  },
  lagging:   { bg: 'rgba(248,113,113,0.06)', label: 'rgba(248,113,113,0.50)' },
  improving: { bg: 'rgba(125,211,252,0.06)', label: 'rgba(125,211,252,0.50)' },
};

// Ticker trail colours — cycle through brand palette
const TRAIL_PALETTE = [
  '#7dd3fc', '#4ade80', '#fbbf24', '#f87171',
  '#a78bfa', '#fb923c', '#60a5fa', '#34d399',
  '#f472b6', '#e879f9',
];

// ── Public ────────────────────────────────────────────────────────────────

export async function loadRRG() {
  const wrap = document.getElementById(WRAP_ID);
  if (!wrap) return;

  _setStatus('Đang tải RRG…');

  try {
    const data = await getJson(API_URL);
    const tickers = data?.tickers ?? [];

    if (!tickers.length) {
      _setStatus('Chưa có thesis active để vẽ RRG.');
      return;
    }

    // Filter out error-only entries (no trail data)
    const valid = tickers.filter(t => t.trail && t.trail.length >= 2);
    if (!valid.length) {
      _setStatus('Dữ liệu giá chưa đủ để tính RRG (cần ≥ 55 phiên).');
      return;
    }

    _clearStatus();
    _render(wrap, valid, data.as_of);
  } catch (err) {
    _setStatus(`Không tải được RRG: ${err.message}`);
  }
}

// ── Private: render ───────────────────────────────────────────────────────

function _render(wrap, tickers, asOf) {
  // Reuse or create canvas
  let canvas = document.getElementById(CANVAS_ID);
  if (!canvas) {
    canvas = document.createElement('canvas');
    canvas.id = CANVAS_ID;
    canvas.className = 'rrg-canvas';
    wrap.appendChild(canvas);
  }

  // DPR-aware sizing
  const dpr  = window.devicePixelRatio || 1;
  const size = wrap.clientWidth || 320;
  canvas.width  = size * dpr;
  canvas.height = size * dpr;   // square
  canvas.style.width  = size + 'px';
  canvas.style.height = size + 'px';

  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  const W = size, H = size;
  const PAD = 36;            // padding for axis labels
  const plotW = W - PAD * 2;
  const plotH = H - PAD * 2;

  // ── Determine axis range ────────────────────────────────────────
  // Collect all (r, m) points from all trails
  const allR = tickers.flatMap(t => t.trail.map(p => p.rs_ratio));
  const allM = tickers.flatMap(t => t.trail.map(p => p.rs_momentum));
  const pad  = 1.5;  // 1.5 unit padding around data

  const rMin = Math.min(100 - pad, ...allR) - pad;
  const rMax = Math.max(100 + pad, ...allR) + pad;
  const mMin = Math.min(100 - pad, ...allM) - pad;
  const mMax = Math.max(100 + pad, ...allM) + pad;

  // Ensure square-ish: symmetric range around 100
  const rSpan = rMax - rMin;
  const mSpan = mMax - mMin;
  const span  = Math.max(rSpan, mSpan);
  const rMid  = (rMin + rMax) / 2;
  const mMid  = (mMin + mMax) / 2;
  const finalRMin = rMid - span / 2;
  const finalRMax = rMid + span / 2;
  const finalMMin = mMid - span / 2;
  const finalMMax = mMid + span / 2;

  // Coordinate converters
  const toX = r => PAD + ((r - finalRMin) / (finalRMax - finalRMin)) * plotW;
  const toY = m => PAD + ((finalMMax - m) / (finalMMax - finalMMin)) * plotH;  // Y flipped

  const cx = toX(100);  // centre X (RS-Ratio = 100)
  const cy = toY(100);  // centre Y (RS-Momentum = 100)

  ctx.clearRect(0, 0, W, H);

  // ── Quadrant backgrounds ──────────────────────────────────────────
  const quads = [
    { key: 'leading',   x: cx,   y: PAD,  w: W - cx - PAD,   h: cy - PAD   },
    { key: 'weakening', x: cx,   y: cy,   w: W - cx - PAD,   h: H - cy - PAD },
    { key: 'lagging',   x: PAD,  y: cy,   w: cx - PAD,       h: H - cy - PAD },
    { key: 'improving', x: PAD,  y: PAD,  w: cx - PAD,       h: cy - PAD   },
  ];
  quads.forEach(q => {
    ctx.fillStyle = Q_COLORS[q.key].bg;
    ctx.fillRect(q.x, q.y, q.w, q.h);
  });

  // ── Quadrant labels ───────────────────────────────────────────────
  const LABEL_TEXT = {
    leading:   'Leading',
    weakening: 'Weakening',
    lagging:   'Lagging',
    improving: 'Improving',
  };
  ctx.font = `600 9px system-ui, sans-serif`;
  ctx.textAlign = 'center';
  quads.forEach(q => {
    ctx.fillStyle = Q_COLORS[q.key].label;
    ctx.fillText(
      LABEL_TEXT[q.key],
      q.x + q.w / 2,
      q.y + q.h / 2 + 3,
    );
  });

  // ── Grid lines (centre cross) ─────────────────────────────────────
  ctx.strokeStyle = 'rgba(148,163,184,0.25)';
  ctx.lineWidth   = 1;
  ctx.setLineDash([3, 3]);
  // Vertical centre
  ctx.beginPath(); ctx.moveTo(cx, PAD); ctx.lineTo(cx, H - PAD); ctx.stroke();
  // Horizontal centre
  ctx.beginPath(); ctx.moveTo(PAD, cy); ctx.lineTo(W - PAD, cy); ctx.stroke();
  ctx.setLineDash([]);

  // ── Axis labels ───────────────────────────────────────────────────
  ctx.fillStyle = 'rgba(148,163,184,0.60)';
  ctx.font      = '8px system-ui, sans-serif';
  ctx.textAlign = 'center';
  // X axis label
  ctx.fillText('RS-Ratio →', W / 2, H - 6);
  // Y axis label (rotated)
  ctx.save();
  ctx.translate(10, H / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText('RS-Momentum ↑', 0, 0);
  ctx.restore();

  // ── Ticker trails ─────────────────────────────────────────────────
  tickers.forEach((ticker, idx) => {
    const color = TRAIL_PALETTE[idx % TRAIL_PALETTE.length];
    const trail = ticker.trail;

    // Draw trail line
    ctx.beginPath();
    ctx.strokeStyle = color + '60';  // 38% alpha
    ctx.lineWidth   = 1.2;
    trail.forEach((pt, i) => {
      const x = toX(pt.rs_ratio);
      const y = toY(pt.rs_momentum);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // Draw trail dots (older = smaller + more transparent)
    trail.forEach((pt, i) => {
      const x      = toX(pt.rs_ratio);
      const y      = toY(pt.rs_momentum);
      const isHead = i === trail.length - 1;
      const radius = isHead ? 4.5 : 2.0;
      const alpha  = isHead ? 1.0 : 0.25 + (i / trail.length) * 0.5;

      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fillStyle = isHead ? color : color + Math.round(alpha * 255).toString(16).padStart(2, '0');
      ctx.fill();

      if (isHead) {
        // Outer ring on head
        ctx.beginPath();
        ctx.arc(x, y, radius + 2, 0, Math.PI * 2);
        ctx.strokeStyle = color + '50';
        ctx.lineWidth = 1;
        ctx.stroke();
      }
    });

    // Label at head position
    const head = trail[trail.length - 1];
    const hx   = toX(head.rs_ratio);
    const hy   = toY(head.rs_momentum);

    ctx.font      = `bold 9px system-ui, sans-serif`;
    ctx.fillStyle = color;
    ctx.textAlign = 'left';
    // Nudge label to avoid overlap with dot
    const lx = hx + 6;
    const ly = hy - 4;
    // Background pill for readability
    const label  = ticker.ticker;
    const metrics = ctx.measureText(label);
    ctx.fillStyle = 'rgba(8,17,31,0.72)';
    ctx.fillRect(lx - 2, ly - 9, metrics.width + 6, 13);
    ctx.fillStyle = color;
    ctx.fillText(label, lx, ly);
  });

  // ── Date stamp ───────────────────────────────────────────────────
  if (asOf) {
    ctx.font      = '8px system-ui, sans-serif';
    ctx.fillStyle = 'rgba(97,113,143,0.70)';
    ctx.textAlign = 'right';
    ctx.fillText(`as of ${asOf}`, W - PAD, H - 6);
  }

  // ── Legend strip ─────────────────────────────────────────────────
  _renderLegend(wrap, tickers);
}

// ── Legend ────────────────────────────────────────────────────────────────

function _renderLegend(wrap, tickers) {
  let legend = wrap.querySelector('.rrg-legend');
  if (!legend) {
    legend = document.createElement('div');
    legend.className = 'rrg-legend';
    wrap.appendChild(legend);
  }

  legend.innerHTML = tickers.map((t, idx) => {
    const color = TRAIL_PALETTE[idx % TRAIL_PALETTE.length];
    const qCls  = `rrg-q--${t.quadrant}`;
    return `<span class="rrg-legend-item">
      <span class="rrg-dot" style="background:${color}"></span>
      <span class="rrg-ticker">${_esc(t.ticker)}</span>
      <span class="rrg-badge ${qCls}">${_esc(t.quadrant)}</span>
    </span>`;
  }).join('');
}

// ── Helpers ───────────────────────────────────────────────────────────────

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
