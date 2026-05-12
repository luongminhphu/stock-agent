/**
 * render-conviction-timeline.js
 * Owner: modules/thesis  (readmodel render layer)
 * Responsibility: fetch + render Conviction Timeline — dual-axis chart (score × price),
 *                 event list, breakdown drawer.
 *
 * Chart layout:
 *   Y-left  (y)  — Conviction score 0–100, tier zones as background
 *   Y-right (y1) — Price VND, entry price dashed annotation
 *   Both datasets share the same X-axis and canvas.
 *
 * ConvictionPoint field mapping (aligned with readmodel/schemas.py):
 *   p.price              — price_at_snapshot (float|null)
 *   p.kind               — "snapshot" | "reviewed"
 *   p.reasoning_summary  — truncated reasoning string|null
 *   p.risk_signals       — string[]
 *
 * Backward compat: convictionTimelineSlotHTML() and loadConvictionTimeline() unchanged.
 */

import { esc, fmtDate } from '../../utils/format.js';
import { thesisApiBase, getJson } from '../../api/client.js';

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

const TIER = [
  { min: 0,  max: 30,  label: 'Critical', color: '#d163a7' },
  { min: 30, max: 50,  label: 'Weak',     color: '#fdab43' },
  { min: 50, max: 65,  label: 'Moderate', color: '#e8af34' },
  { min: 65, max: 80,  label: 'Healthy',  color: '#6daa45' },
  { min: 80, max: 100, label: 'Strong',   color: '#4f98a3' },
];

function tierColor(score) {
  const s = Number(score);
  if (s >= 80) return '#4f98a3';
  if (s >= 65) return '#6daa45';
  if (s >= 50) return '#e8af34';
  if (s >= 30) return '#fdab43';
  return '#d163a7';
}

const TREND_META = {
  improving:         { icon: '↑', label: 'Improving',        cls: 'cv-trend--up' },
  declining:         { icon: '↓', label: 'Declining',        cls: 'cv-trend--down' },
  stable:            { icon: '→', label: 'Stable',           cls: 'cv-trend--stable' },
  insufficient_data: { icon: '—', label: 'Insufficient data', cls: '' },
};

const BD_META = [
  { key: 'assumption_health', label: 'Assumption Health', color: '#6daa45' },
  { key: 'catalyst_progress', label: 'Catalyst Progress', color: '#4f98a3' },
  { key: 'risk_reward',       label: 'Risk / Reward',     color: '#e8af34' },
  { key: 'review_confidence', label: 'AI Confidence',     color: '#d163a7' },
];

const VERDICT_CLS = {
  BUY:      'cv-vtag--buy',
  HOLD:     'cv-vtag--hold',
  REDUCE:   'cv-vtag--reduce',
  SELL:     'cv-vtag--sell',
  BULLISH:  'cv-vtag--buy',
  BEARISH:  'cv-vtag--sell',
  NEUTRAL:  'cv-vtag--hold',
  WATCHLIST:'cv-vtag--hold',
};

const EVENT_KIND_ICON = {
  reviewed: '🤖',
  snapshot: '📸',
  created:  '🔬',
  updated:  '✏️',
};

// ─────────────────────────────────────────────────────────────────────────────
// Lazy CDN loader
// ─────────────────────────────────────────────────────────────────────────────

let _chartJsReady = null;

function ensureChartJs() {
  if (_chartJsReady) return _chartJsReady;
  if (window.Chart && window.Chart.registry?.plugins?.get('annotation')) {
    _chartJsReady = Promise.resolve();
    return _chartJsReady;
  }
  _chartJsReady = new Promise((resolve, reject) => {
    function loadScript(src, onload) {
      const s = document.createElement('script');
      s.src = src; s.defer = true;
      s.onload = onload;
      s.onerror = () => reject(new Error('Failed to load ' + src));
      document.head.appendChild(s);
    }
    if (!window.Chart) {
      loadScript(
        'https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js',
        () => loadScript(
          'https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js',
          () => { Chart.register(window['chartjs-plugin-annotation']); resolve(); }
        )
      );
    } else {
      loadScript(
        'https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js',
        () => { Chart.register(window['chartjs-plugin-annotation']); resolve(); }
      );
    }
  });
  return _chartJsReady;
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Convert 6-digit hex → rgba(r,g,b,alpha). */
function hexToRgba(hex, alpha) {
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function parsePoints(points) {
  const labels = points.map(p => fmtDate(p.snapshotted_at));
  const scores = points.map(p => Number(p.score ?? 0));
  const prices = points.map(p => p.price != null ? Number(p.price) : null);

  const events = points.map((p, idx) => ({
    idx,
    kind:       p.kind ?? 'snapshot',
    verdict:    p.verdict ? String(p.verdict).toUpperCase() : null,
    confidence: p.confidence != null
                  ? Math.round(Number(p.confidence) * (Number(p.confidence) <= 1 ? 100 : 1))
                  : null,
    score:      Number(p.score ?? 0),
    price:      p.price != null ? Number(p.price) : null,
    date:       p.snapshotted_at,
    reasoning:  p.reasoning_summary ?? null,
    risks:      Array.isArray(p.risk_signals) ? p.risk_signals : [],
    breakdown:  p.breakdown ?? null,
  }));

  return { labels, scores, prices, events };
}

// ─────────────────────────────────────────────────────────────────────────────
// Dual-axis chart annotations
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Build all annotations for the merged dual-axis chart:
 *  - Tier background zones (scaleID: 'y' — left axis, score)
 *  - Entry price horizontal line (scaleID: 'y1' — right axis, price)
 *  - AI review vertical lines (cross both axes, no scaleID needed)
 */
function buildDualAnnotations(events, entryPrice) {
  const anns = {};

  // Tier zones — bound to left score axis
  TIER.forEach((t, i) => {
    anns[`zone${i}`] = {
      type: 'box',
      yScaleID: 'y',
      yMin: t.min,
      yMax: t.max,
      backgroundColor: hexToRgba(t.color, 0.07),
      borderWidth: 0,
      drawTime: 'beforeDatasetsDraw',
    };
  });

  // Entry price line — bound to right price axis
  if (entryPrice) {
    anns.entry = {
      type: 'line',
      yScaleID: 'y1',
      yMin: entryPrice,
      yMax: entryPrice,
      borderColor: 'rgba(180,180,180,.4)',
      borderWidth: 1.2,
      borderDash: [5, 4],
      drawTime: 'beforeDatasetsDraw',
      label: {
        content: 'Entry',
        display: true,
        position: 'end',
        color: 'rgba(180,180,180,.7)',
        font: { size: 9 },
        padding: { x: 4, y: 2 },
      },
    };
  }

  // AI review vertical lines — cross entire chart height
  events.forEach((e, i) => {
    if (e.kind !== 'reviewed') return;
    anns[`evLine${i}`] = {
      type: 'line',
      xMin: e.idx,
      xMax: e.idx,
      borderColor: 'rgba(109,170,69,.5)',
      borderWidth: 1.5,
      borderDash: [5, 3],
      drawTime: 'beforeDatasetsDraw',
    };
  });

  return anns;
}

// ─────────────────────────────────────────────────────────────────────────────
// Chart renderer — single dual-axis canvas
// ─────────────────────────────────────────────────────────────────────────────

const _chartInstances = new Map();

function destroyCharts(ticker) {
  const key = `${ticker}:dual`;
  if (_chartInstances.has(key)) { _chartInstances.get(key).destroy(); _chartInstances.delete(key); }
}

function buildDualChart(canvasEl, { labels, scores, prices, events, entryPrice }) {
  const ctx = canvasEl.getContext('2d');
  const hasPrices = prices.some(p => p != null);

  // Gradients
  const gradScore = ctx.createLinearGradient(0, 0, 0, 260);
  gradScore.addColorStop(0, 'rgba(79,152,163,.22)');
  gradScore.addColorStop(1, 'rgba(79,152,163,0)');

  const gradPrice = ctx.createLinearGradient(0, 0, 0, 260);
  gradPrice.addColorStop(0, 'rgba(232,175,52,.15)');
  gradPrice.addColorStop(1, 'rgba(232,175,52,0)');

  const muted   = cssVar('--muted')       || '#797876';
  const surface = cssVar('--surface-dyn') || '#2d2c2a';
  const border  = cssVar('--border')      || '#393836';
  const primary = cssVar('--primary')     || '#4f98a3';
  const gold    = cssVar('--gold')        || '#e8af34';
  const gridColor = 'rgba(128,128,128,.06)';
  const tickFont  = { size: 10, family: "'Satoshi', system-ui, sans-serif" };

  const datasets = [
    {
      label: 'Conviction',
      data: scores,
      yAxisID: 'y',
      borderColor: primary,
      backgroundColor: gradScore,
      borderWidth: 2.5,
      tension: 0.4,
      fill: true,
      pointRadius: 4,
      pointHoverRadius: 7,
      pointBackgroundColor: scores.map(tierColor),
      pointBorderColor: primary,
      pointBorderWidth: 1.5,
      order: 1,
    },
  ];

  if (hasPrices) {
    datasets.push({
      label: 'Giá',
      data: prices,
      yAxisID: 'y1',
      borderColor: gold,
      backgroundColor: gradPrice,
      borderWidth: 2,
      tension: 0.4,
      fill: true,
      pointRadius: 2.5,
      pointHoverRadius: 6,
      pointBackgroundColor: gold,
      spanGaps: true,
      order: 2,
    });
  }

  return new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: surface,
          titleColor: cssVar('--text') || '#cdccca',
          bodyColor: muted,
          borderColor: border,
          borderWidth: 1,
          padding: 10,
          callbacks: {
            title: c => '📅 ' + c[0].label,
            label: c => {
              if (c.dataset.label === 'Conviction') {
                return `Conviction: ${Number(c.parsed.y).toFixed(1)} / 100`;
              }
              return c.parsed.y != null
                ? `Giá: ${Number(c.parsed.y).toLocaleString('vi-VN')}₫`
                : 'Giá: N/A';
            },
          },
        },
        annotation: { annotations: buildDualAnnotations(events, entryPrice) },
      },
      scales: {
        x: {
          grid: { color: gridColor, drawTicks: false },
          border: { display: false },
          ticks: { color: muted, font: tickFont, maxRotation: 0, maxTicksLimit: 8 },
        },
        // Left axis — Conviction score 0–100
        y: {
          type: 'linear',
          position: 'left',
          min: 0,
          max: 100,
          grid: { color: gridColor, drawTicks: false },
          border: { display: false },
          ticks: { color: primary, font: tickFont, stepSize: 20 },
          title: { display: true, text: 'Score', color: primary, font: { size: 9 } },
        },
        // Right axis — Price VND (only if price data present)
        ...(hasPrices ? {
          y1: {
            type: 'linear',
            position: 'right',
            grid: { drawOnChartArea: false },
            border: { display: false },
            ticks: {
              color: gold,
              font: { size: 10 },
              callback: v => (v / 1000).toFixed(0) + 'k',
            },
            title: { display: true, text: 'Giá', color: gold, font: { size: 9 } },
          },
        } : {}),
      },
    },
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Event list HTML
// ─────────────────────────────────────────────────────────────────────────────

function renderEventList(events) {
  if (!events.length) return '<p class="cv-empty">Chưa có sự kiện nào.</p>';
  const shown = events.filter(e => e.kind === 'reviewed' || e.verdict != null || e === events[events.length - 1]);
  const list = shown.length ? shown : events.slice(-5);
  return list.map(e => {
    const icon  = EVENT_KIND_ICON[e.kind] || '📌';
    const vtag  = e.verdict
      ? `<span class="cv-vtag ${VERDICT_CLS[e.verdict] || 'cv-vtag--hold'}">${esc(e.verdict)}</span>`
      : '';
    const conf  = e.confidence != null ? `<span class="cv-chip">Conf ${e.confidence}%</span>` : '';
    const price = e.price ? `<span class="cv-chip">${Number(e.price).toLocaleString('vi-VN')}₫</span>` : '';
    return `
      <div class="cv-ev" data-ev-idx="${e.idx}" role="button" tabindex="0" aria-expanded="false">
        <span class="cv-ev-icon">${icon}</span>
        <div class="cv-ev-body">
          <div class="cv-ev-date">${fmtDate(e.date)} · ${esc(e.kind)}</div>
          <div class="cv-ev-score">Score <strong>${e.score.toFixed(1)}</strong></div>
          <div class="cv-ev-meta">${vtag}${conf}${price}</div>
        </div>
      </div>`;
  }).join('');
}

// ─────────────────────────────────────────────────────────────────────────────
// Detail drawer HTML
// ─────────────────────────────────────────────────────────────────────────────

function renderDrawer(e) {
  const bdBars = e.breakdown
    ? BD_META.map(m => {
        const val = Number(e.breakdown[m.key] ?? 0);
        return `
          <div class="cv-bd-item">
            <div class="cv-bd-label">${esc(m.label)}</div>
            <div class="cv-bd-track"><div class="cv-bd-fill" style="width:${val}%;background:${m.color}"></div></div>
            <div class="cv-bd-val">${val.toFixed(0)}/100</div>
          </div>`;
      }).join('')
    : '<p class="cv-empty" style="font-size:.78rem;">Chưa có breakdown.</p>';

  const risks = e.risks?.length
    ? `<div class="cv-risk-block">
        <div class="cv-risk-title">⚠ Risk signals</div>
        ${e.risks.map(r => `<div class="cv-risk-item">${esc(r)}</div>`).join('')}
       </div>`
    : '';

  const reasoning = e.reasoning
    ? `<div class="cv-reasoning">${esc(e.reasoning)}</div>`
    : '';

  return `
    <div class="cv-drawer">
      <div class="cv-drawer-hd">
        <span class="cv-drawer-kind">${EVENT_KIND_ICON[e.kind] || '📌'} ${esc(e.kind)}</span>
        <span class="cv-drawer-date">${fmtDate(e.date)}</span>
      </div>
      <div class="cv-bd-grid">${bdBars}</div>
      ${risks}
      ${reasoning}
    </div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Wire event list interactions
// ─────────────────────────────────────────────────────────────────────────────

function wireEventList(containerEl, events) {
  const evEls = containerEl.querySelectorAll('.cv-ev');
  let activeIdx = null;

  evEls.forEach(el => {
    const handler = () => {
      const idx = Number(el.dataset.evIdx);
      const ev = events.find(e => e.idx === idx);
      if (!ev) return;

      if (activeIdx === idx) {
        activeIdx = null;
        el.setAttribute('aria-expanded', 'false');
        el.classList.remove('cv-ev--active');
        containerEl.querySelector('.cv-drawer')?.remove();
        return;
      }

      containerEl.querySelectorAll('.cv-ev').forEach(e => {
        e.classList.remove('cv-ev--active');
        e.setAttribute('aria-expanded', 'false');
      });
      containerEl.querySelector('.cv-drawer')?.remove();

      activeIdx = idx;
      el.classList.add('cv-ev--active');
      el.setAttribute('aria-expanded', 'true');

      const drawer = document.createElement('div');
      drawer.innerHTML = renderDrawer(ev);
      el.after(drawer.firstElementChild);
      drawer.firstElementChild?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
    };

    el.addEventListener('click', handler);
    el.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handler(); }
    });
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Main HTML scaffold
// ─────────────────────────────────────────────────────────────────────────────

function buildScaffold({ data, labels, scores, prices, events }) {
  const trend = data.trend ?? 'insufficient_data';
  const tm = TREND_META[trend] ?? TREND_META.insufficient_data;
  const hasPrices = prices.some(p => p != null);
  const delta = data.earliest_score != null && data.latest_score != null
    ? (Number(data.latest_score) - Number(data.earliest_score)).toFixed(1)
    : null;
  const deltaSign  = delta > 0 ? '+' : '';
  const deltaClass = delta > 0 ? 'cv-delta--up' : delta < 0 ? 'cv-delta--down' : '';

  const tierLegend = TIER.map(t =>
    `<div class="cv-tier-pill"><div class="cv-tier-sq" style="background:${t.color}"></div>${t.label}</div>`
  ).join('');

  const shownEvents = events.filter(e => e.kind === 'reviewed' || e.verdict != null || e === events[events.length - 1]);
  const listEvents  = shownEvents.length ? shownEvents : events.slice(-5);

  const entryLabel = data.entry_price
    ? `· Entry: ${Number(data.entry_price).toLocaleString('vi-VN')}₫`
    : '';

  return `
    <div class="cv-section detail-section">
      <!-- Header -->
      <div class="cv-header detail-section-header">
        <div>
          <h3>Conviction Timeline</h3>
          <p class="muted" style="font-size:.78rem;margin-top:2px;">${data.total ?? scores.length} data-points · ${esc(data.ticker ?? '')}</p>
        </div>
        <div class="cv-header-right">
          <span class="cv-trend-badge ${tm.cls}">${tm.icon} ${tm.label}</span>
          ${delta !== null ? `<span class="cv-delta ${deltaClass}">${deltaSign}${delta} pts</span>` : ''}
        </div>
      </div>

      <!-- Dual-axis chart -->
      <div class="cv-chart-card">
        <div class="cv-chart-legend">
          <div class="cv-leg"><div class="cv-leg-dot" style="background:var(--primary,#4f98a3)"></div>Conviction</div>
          ${hasPrices ? `<div class="cv-leg"><div class="cv-leg-dot" style="background:var(--gold,#e8af34)"></div>Giá ${entryLabel}</div>` : ''}
          <div class="cv-leg cv-leg--line" style="--lc:rgba(109,170,69,.7)">AI Review</div>
        </div>
        <div class="cv-canvas-wrap cv-canvas--dual">
          <canvas id="cvChart-${data.ticker}"></canvas>
        </div>
        <div class="cv-tier-strip">${tierLegend}</div>
      </div>

      <!-- Event list -->
      ${listEvents.length ? `
        <div class="cv-event-panel">
          <div class="cv-event-hd">
            <span class="cv-event-title">AI Reviews</span>
            <span class="cv-chip">${listEvents.length}</span>
          </div>
          <div class="cv-event-list" id="cvEventList-${data.ticker}">
            ${renderEventList(events)}
          </div>
        </div>` : ''}
    </div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Public API — backward-compatible
// ─────────────────────────────────────────────────────────────────────────────

export function convictionTimelineSlotHTML(thesisId) {
  return `<div id="convictionTimelineSlot-${thesisId}" data-thesis-id="${thesisId}"></div>`;
}

export function renderConvictionTimeline(data) {
  if (!data || !Array.isArray(data.points) || !data.points.length) {
    return `
      <div class="detail-section">
        <div class="detail-section-header"><h3>Conviction Timeline</h3></div>
        <p class="empty-state">Chưa có snapshot nào. Trigger AI review để tạo điểm dữ liệu đầu tiên.</p>
      </div>`;
  }
  const { labels, scores, prices, events } = parsePoints(data.points);
  return buildScaffold({ data, labels, scores, prices, events });
}

export async function loadConvictionTimeline(thesisId) {
  const slot = document.getElementById(`convictionTimelineSlot-${thesisId}`);
  if (!slot) return;

  slot.innerHTML = `
    <div class="detail-section" aria-busy="true">
      <div class="detail-section-header"><h3>Conviction Timeline</h3></div>
      <div style="margin:12px 0;"><div class="skel" style="height:72px;border-radius:6px;"></div></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        ${[1,2,3].map(() => '<div class="skel skel-badge" style="width:64px;"></div>').join('')}
      </div>
    </div>`;

  try {
    await ensureChartJs();
    const data = await getJson(`${thesisApiBase()}/${thesisId}/conviction-timeline?limit=20`);
    if (!data) { slot.innerHTML = ''; return; }

    const ticker = data.ticker ?? String(thesisId);
    destroyCharts(ticker);

    slot.innerHTML = renderConvictionTimeline(data);

    const { labels, scores, prices, events } = parsePoints(data.points);

    const canvas = document.getElementById(`cvChart-${ticker}`);
    if (canvas) {
      const inst = buildDualChart(canvas, {
        labels, scores, prices, events,
        entryPrice: data.entry_price ?? null,
      });
      _chartInstances.set(`${ticker}:dual`, inst);
    }

    const listEl = document.getElementById(`cvEventList-${ticker}`);
    if (listEl && events.length) wireEventList(listEl, events);

  } catch (err) {
    slot.innerHTML = `
      <div class="detail-section">
        <div class="detail-section-header"><h3>Conviction Timeline</h3></div>
        <p class="empty-state" style="font-size:.8rem;">Chưa tải được timeline: ${esc(err.message)}</p>
      </div>`;
  }
}
