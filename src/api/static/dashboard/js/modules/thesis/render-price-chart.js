/**
 * render-price-chart.js
 * Owner: modules/thesis (market segment)
 */

import { getJson } from '../../api/client.js';
import { fmt }     from '../../utils/format.js';

// ─── Instance registry ────────────────────────────────────────────────────────────────────────
const _chartInstances = new Map();

export function destroyPriceChart(thesisId) {
  const inst = _chartInstances.get(thesisId);
  if (inst) {
    try { inst.destroy(); } catch { /* ignore */ }
    _chartInstances.delete(thesisId);
  }
}

// ─── Resolve CSS variable → computed hex ────────────────────────────────────────────────────
/**
 * Chart.js không resolve CSS custom properties.
 * Hàm này đọc computed style từ document.body — nơi các CSS variables
 * đã được resolve bởi browser — và trả về giá trị màu thực.
 *
 * @param {string} varName  CSS variable name, e.g. '--muted'
 * @param {string} fallback Hex fallback nếu variable không tồn tại
 */
function cssVar(varName, fallback) {
  const val = getComputedStyle(document.body)
    .getPropertyValue(varName)
    .trim();
  return val || fallback;
}

// ─── Slot HTML ─────────────────────────────────────────────────────────────────────────────────
export function priceMiniChartSlotHTML(thesisId) {
  return `
    <div id="priceMiniChartSlot-${thesisId}" class="price-mini-chart-slot" aria-live="polite">
      <div class="price-mini-chart-skeleton">
        <div class="skel" style="width:100%;height:120px;border-radius:6px;"></div>
      </div>
    </div>`;
}

// ─── Fetch OHLCV ────────────────────────────────────────────────────────────────────────────────
async function fetchOhlcv(ticker, days = 30) {
  if (!ticker) return null;
  try {
    return await getJson(
      `/api/v1/market/ohlcv/${encodeURIComponent(ticker.toUpperCase())}?days=${days}`
    );
  } catch {
    return null;
  }
}

// ─── Helpers ───────────────────────────────────────────────────────────────────────────────────
function normalizeOhlcv(raw) {
  const arr = Array.isArray(raw) ? raw : (raw?.items ?? raw?.data ?? []);
  return arr
    .filter(d => d && d.close != null)
    .map(d => ({
      date:  d.date ?? d.trading_date ?? d.t ?? '',
      close: Number(d.close),
    }))
    .sort((a, b) => a.date < b.date ? -1 : a.date > b.date ? 1 : 0);
}

function annotationLine(price, label, color) {
  if (price == null || price <= 0) return null;
  return {
    type:        'line',
    scaleID:     'y',
    value:       price,
    borderColor: color,
    borderWidth: 1.5,
    borderDash:  [4, 3],
    label: {
      display:         true,
      content:         `${label} ${fmt(price)}₫`,
      position:        'start',
      backgroundColor: color + '22',
      color:           color,
      font:            { size: 10, weight: 'normal' },
      padding:         { x: 4, y: 2 },
      yAdjust:         -8,
    },
  };
}

// ─── Render ───────────────────────────────────────────────────────────────────────────────────────

function renderUnavailable(slot) {
  slot.innerHTML = `
    <div class="price-mini-chart-unavailable">
      <span>Biểu đồ giá không khả dụng</span>
    </div>`;
}

export async function loadPriceMiniChart(thesis, slot) {
  if (!slot) return;

  const raw = await fetchOhlcv(thesis.ticker, 30);
  if (!slot.isConnected) return;

  const points = normalizeOhlcv(raw);
  if (!points.length) {
    renderUnavailable(slot);
    return;
  }

  destroyPriceChart(thesis.id);

  slot.innerHTML = `
    <div class="price-mini-chart-wrap">
      <div class="price-mini-chart-header">
        <span class="price-mini-chart-title">Giá đóng cửa 30 ngày</span>
      </div>
      <div class="price-mini-chart-canvas-wrap">
        <canvas
          id="priceMiniCanvas-${thesis.id}"
          aria-label="Biểu đồ giá ${thesis.ticker} 30 ngày"
          style="width:100%;height:120px;"
        ></canvas>
      </div>
    </div>`;

  const canvas = slot.querySelector(`#priceMiniCanvas-${thesis.id}`);
  if (!canvas) return;

  if (!window.Chart) {
    await new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js';
      s.onload  = resolve;
      s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  // ─── Resolve màu tại render-time (sau khi DOM + CSS đã được paint) ───
  // Chart.js nhận string thần, không resolve var(). Dùng getComputedStyle.
  const tickColor = cssVar('--muted',       '#94a3b8');  // labels trục x/y
  const gridColor = cssVar('--chart-grid',  'rgba(148,163,184,0.10)');  // đường kẻ nẹ
  const tooltipBg = cssVar('--surface',     '#1e293b');  // tooltip background
  const tooltipFg = cssVar('--text',        '#e2e8f0');  // tooltip text

  const first = points[0].close;
  const last  = points[points.length - 1].close;
  const lineColor = last >= first ? '#22c55e' : '#ef4444';
  const fillColor = last >= first ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)';

  const labels = points.map(d => {
    const dt = new Date(d.date);
    if (isNaN(dt)) return d.date?.slice(5) || d.date;
    return `${dt.getMonth() + 1}/${dt.getDate()}`;
  });

  const data = points.map(d => d.close);

  // Annotations: entry, target, stop_loss
  const annotations = {};
  const al = annotationLine(thesis.entry_price,  'Entry',  '#6366f1');
  const tl = annotationLine(thesis.target_price, 'Target', '#22c55e');
  const sl = annotationLine(thesis.stop_loss,    'Stop',   '#ef4444');
  if (al) annotations.entry  = al;
  if (tl) annotations.target = tl;
  if (sl) annotations.stop   = sl;

  const hasAnnotation = window.Chart?.registry?.plugins?.get('annotation');
  if (!hasAnnotation && Object.keys(annotations).length) {
    await new Promise((resolve) => {
      const s = document.createElement('script');
      s.src = 'https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js';
      s.onload  = resolve;
      s.onerror = () => resolve();
      document.head.appendChild(s);
    });
  }

  const inst = new window.Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        data,
        borderColor:               lineColor,
        backgroundColor:           fillColor,
        fill:                      true,
        tension:                   0.3,
        pointRadius:               0,
        pointHoverRadius:          4,
        pointHoverBackgroundColor: lineColor,
        borderWidth:               1.5,
      }],
    },
    options: {
      responsive:          true,
      maintainAspectRatio: false,
      animation:           { duration: 400 },
      interaction:         { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: tooltipBg,
          titleColor:      tooltipFg,
          bodyColor:       tooltipFg,
          borderColor:     'rgba(148,163,184,0.15)',
          borderWidth:     1,
          padding:         6,
          callbacks: {
            label: ctx => ` ${fmt(ctx.parsed.y)}₫`,
          },
        },
        annotation: Object.keys(annotations).length
          ? { annotations }
          : undefined,
      },
      scales: {
        x: {
          ticks: {
            color:         tickColor,   // ← resolved hex, không phải CSS var string
            font:          { size: 10 },
            maxTicksLimit: 6,
            maxRotation:   0,
          },
          grid: { display: false },
        },
        y: {
          position: 'right',
          ticks: {
            color:         tickColor,   // ← idem
            font:          { size: 10 },
            maxTicksLimit: 4,
            callback:      v => fmt(v) + '₫',
          },
          grid: {
            color: gridColor,           // ← idem
          },
        },
      },
    },
  });

  _chartInstances.set(thesis.id, inst);
}
