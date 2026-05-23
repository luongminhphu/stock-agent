/**
 * chart-utils.js
 * Owner: modules/thesis/conviction-timeline
 * Responsibility: Chart.js lazy loader, canvas helpers, dual-axis chart builder.
 */

import { TIER, tierColor } from './constants.js';

// ─────────────────────────────────────────────────────────────────────────────
// Lazy CDN loader
// crossOrigin='anonymous' suppresses Edge/Safari ITP storage-access warnings
// ─────────────────────────────────────────────────────────────────────────────

let _chartJsReady = null;

export function ensureChartJs() {
  if (_chartJsReady) return _chartJsReady;
  if (window.Chart && window.Chart.registry?.plugins?.get('annotation')) {
    _chartJsReady = Promise.resolve();
    return _chartJsReady;
  }
  _chartJsReady = new Promise((resolve, reject) => {
    function loadScript(src, onload) {
      const s = document.createElement('script');
      s.src = src;
      s.defer = true;
      s.crossOrigin = 'anonymous';  // suppress ITP storage-access warnings
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
// Canvas helpers
// ─────────────────────────────────────────────────────────────────────────────

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Convert 6-digit hex → rgba(r,g,b,alpha). */
export function hexToRgba(hex, alpha) {
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Annotations builder
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Build all annotations for the merged dual-axis chart:
 *  - Tier background zones (scaleID: 'y' — left axis, score)
 *  - Entry price horizontal line (scaleID: 'y1' — right axis, price)
 *  - AI review vertical lines
 */
export function buildDualAnnotations(events, entryPrice) {
  const anns = {};

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
// Dual-axis chart builder
// ─────────────────────────────────────────────────────────────────────────────

const _chartInstances = new Map();

export function destroyCharts(ticker) {
  const key = `${ticker}:dual`;
  if (_chartInstances.has(key)) { _chartInstances.get(key).destroy(); _chartInstances.delete(key); }
}

export function buildDualChart(canvasEl, { labels, scores, prices, events, entryPrice }) {
  const ctx = canvasEl.getContext('2d');
  const hasPrices = prices.some(p => p != null);

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

  const chart = new Chart(ctx, {
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
                return `Conviction: ${Number(c.parsed.y).toFixed(1)}`;
              }
              if (c.dataset.label === 'Giá') {
                return `Giá: ${Number(c.parsed.y).toLocaleString('vi-VN')}₫`;
              }
              return c.dataset.label + ': ' + c.parsed.y;
            },
          },
        },
        annotation: { annotations: buildDualAnnotations(events, entryPrice) },
      },
      scales: {
        x: {
          grid: { color: gridColor },
          ticks: { color: muted, font: tickFont, maxRotation: 30, autoSkip: true, maxTicksLimit: 8 },
        },
        y: {
          min: 0,
          max: 100,
          position: 'left',
          grid: { color: gridColor },
          ticks: { color: muted, font: tickFont, stepSize: 20,
            callback: v => v === 0 ? '' : v,
          },
        },
        y1: hasPrices ? {
          position: 'right',
          grid: { drawOnChartArea: false },
          ticks: {
            color: gold,
            font: tickFont,
            callback: v => Number(v).toLocaleString('vi-VN', { notation: 'compact', maximumFractionDigits: 0 }),
          },
        } : undefined,
      },
    },
  });

  _chartInstances.set(`${canvasEl.id.replace('cvChart-', '')}:dual`, chart);
  return chart;
}
