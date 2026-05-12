/**
 * render-price-chart.js
 * Owner: modules/thesis (market segment)
 * Responsibility: fetch OHLCV 30 ngày gần nhất cho ticker, render mini line
 *   chart (Chart.js) với annotation lines cho entry / target / stop_loss.
 *
 * Public API:
 *   priceMiniChartSlotHTML(thesisId)  → string  (slot placeholder)
 *   loadPriceMiniChart(thesis, slot)  → Promise<void>
 *   destroyPriceChart(thesisId)       → void
 */

import { getJson } from '../../api/client.js';
import { fmt }     from '../../utils/format.js';

// ─── Instance registry (tránh Chart.js leak) ─────────────────────────────────
const _chartInstances = new Map();

export function destroyPriceChart(thesisId) {
  const inst = _chartInstances.get(thesisId);
  if (inst) {
    try { inst.destroy(); } catch { /* ignore */ }
    _chartInstances.delete(thesisId);
  }
}

// ─── Slot HTML ────────────────────────────────────────────────────────────────

export function priceMiniChartSlotHTML(thesisId) {
  return `
    <div id="priceMiniChartSlot-${thesisId}" class="price-mini-chart-slot" aria-live="polite">
      <div class="price-mini-chart-skeleton">
        <div class="skel" style="width:100%;height:120px;border-radius:6px;"></div>
      </div>
    </div>`;
}

// ─── Fetch OHLCV ─────────────────────────────────────────────────────────────

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

// ─── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Chuẩn hóa response OHLCV thành array of { date, close }.
 * API có thể trả về:
 *   - array of { date, close, open, high, low, volume }
 *   - { items: [...] }  hoặc  { data: [...] }
 */
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

/**
 * Tạo annotation line config cho Chart.js annotation plugin.
 * @param {number|null} price
 * @param {string}       label
 * @param {string}       color
 */
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

// ─── Render ───────────────────────────────────────────────────────────────────

/**
 * Render fallback HTML khi không lấy được dữ liệu.
 */
function renderUnavailable(slot) {
  slot.innerHTML = `
    <div class="price-mini-chart-unavailable">
      <span>Biểu đồ giá không khả dụng</span>
    </div>`;
}

/**
 * Load và render mini price chart vào slot.
 * @param {{ id, ticker, entry_price, target_price, stop_loss }} thesis
 * @param {HTMLElement} slot
 */
export async function loadPriceMiniChart(thesis, slot) {
  if (!slot) return;

  const raw = await fetchOhlcv(thesis.ticker, 30);
  if (!slot.isConnected) return; // slot đã bị unmount

  const points = normalizeOhlcv(raw);
  if (!points.length) {
    renderUnavailable(slot);
    return;
  }

  // Huỷ instance cũ nếu có
  destroyPriceChart(thesis.id);

  // Build DOM
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

  // Lazy-load Chart.js từ CDN nếu chưa có
  if (!window.Chart) {
    await new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js';
      s.onload  = resolve;
      s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  // Màu đường giá: xanh nếu close[-1] >= close[0], đỏ nếu ngược lại
  const first = points[0].close;
  const last  = points[points.length - 1].close;
  const lineColor = last >= first ? '#22c55e' : '#ef4444';
  const fillColor = last >= first ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)';

  const labels = points.map(d => {
    const s = d.date;
    if (!s) return '';
    // Hiển thị MM/DD hoặc nguyên nếu không parse được
    const dt = new Date(s);
    if (isNaN(dt)) return s.slice(5) || s; // YYYY-MM-DD → MM-DD
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

  // Đăng ký plugin annotation nếu CDN chưa inject
  const hasAnnotation = window.Chart?.registry?.plugins?.get('annotation');
  if (!hasAnnotation && Object.keys(annotations).length) {
    await new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = 'https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js';
      s.onload  = resolve;
      s.onerror = () => resolve(); // graceful — chart renders without annotations
      document.head.appendChild(s);
    });
  }

  const inst = new window.Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        data,
        borderColor:          lineColor,
        backgroundColor:      fillColor,
        fill:                 true,
        tension:              0.3,
        pointRadius:          0,
        pointHoverRadius:     4,
        pointHoverBackgroundColor: lineColor,
        borderWidth:          1.5,
      }],
    },
    options: {
      responsive:          true,
      maintainAspectRatio: false,
      animation:           { duration: 400 },
      interaction:         { mode: 'index', intersect: false },
      plugins: {
        legend:   { display: false },
        tooltip: {
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
            color:    'var(--muted, #888)',
            font:     { size: 10 },
            maxTicksLimit: 6,
            maxRotation: 0,
          },
          grid: { display: false },
        },
        y: {
          position: 'right',
          ticks: {
            color:    'var(--muted, #888)',
            font:     { size: 10 },
            maxTicksLimit: 4,
            callback: v => fmt(v) + '₫',
          },
          grid: {
            color: 'rgba(128,128,128,0.08)',
          },
        },
      },
    },
  });

  _chartInstances.set(thesis.id, inst);
}
