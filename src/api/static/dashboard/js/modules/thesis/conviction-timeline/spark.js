/**
 * spark.js
 * Owner: modules/thesis/conviction-timeline
 * Responsibility: Spark (mini inline) chart — used in thesis table rows.
 *   Fetches conviction-timeline and renders a compact Chart.js line.
 *   Designed as progressive enhancement — silent fail, never blocks table render.
 */

import { tierColor } from './constants.js';
import { ensureChartJs, hexToRgba } from './chart-utils.js';
import { thesisApiBase, getJson } from '../../../api/client.js';

const _sparkInstances = new Map();

export function destroySpark(thesisId) {
  const key = `spark:${thesisId}`;
  if (_sparkInstances.has(key)) {
    try { _sparkInstances.get(key).destroy(); } catch { /* ignore */ }
    _sparkInstances.delete(key);
  }
}

export function renderSparkChart(canvasEl, points, thesisId) {
  destroySpark(thesisId);
  if (!points?.length) return;

  // Bug #1 guard: canvas đã bị detach khỏi DOM (table re-render trong lúc await)
  if (!canvasEl.isConnected) return;

  // Bug #2 guard: window.Chart chưa sẵn dù ensureChartJs() đã resolve
  if (typeof window.Chart === 'undefined') return;

  const scores = points.map(p => Number(p.score ?? 0));
  const latest = scores[scores.length - 1];
  const color  = tierColor(latest);

  let ctx;
  try {
    ctx = canvasEl.getContext('2d');
  } catch {
    return; // canvas context không khả dụng (detach race)
  }
  if (!ctx) return;

  const grad = ctx.createLinearGradient(0, 0, 0, 40);
  grad.addColorStop(0, hexToRgba(color, 0.3));
  grad.addColorStop(1, hexToRgba(color, 0));

  const chart = new window.Chart(ctx, {
    type: 'line',
    data: {
      labels: scores.map((_, i) => i),
      datasets: [{
        data: scores,
        borderColor:     color,
        backgroundColor: grad,
        borderWidth:     1.5,
        tension:         0.4,
        fill:            true,
        pointRadius:     0,
        pointHoverRadius: 3,
      }],
    },
    options: {
      responsive: false,
      animation:  false,
      plugins: {
        legend:  { display: false },
        tooltip: { enabled: false },
      },
      scales: {
        x: { display: false },
        y: { display: false, min: 0, max: 100 },
      },
    },
  });

  _sparkInstances.set(`spark:${thesisId}`, chart);
  return chart;
}

export async function loadSparkChart(thesisId, canvasEl) {
  try {
    await ensureChartJs();

    // Bug #2: chờ thêm nếu CDN script chưa execute xong
    if (typeof window.Chart === 'undefined') {
      await new Promise(r => setTimeout(r, 300));
    }
    if (typeof window.Chart === 'undefined') return; // CDN lỗi hẳn, bỏ qua

    // Bug #1: kiểm tra sau ensureChartJs() — table có thể đã re-render trong lúc chờ
    if (!canvasEl.isConnected) return;

    const data = await getJson(`${thesisApiBase()}/${thesisId}/conviction-timeline`);

    // Bug #1: kiểm tra lần 2 sau getJson() — đây là điểm dễ race nhất (~200–500ms)
    if (!canvasEl.isConnected) return;

    if (!data?.points?.length) return;

    renderSparkChart(canvasEl, data.points, thesisId);
  } catch {
    // silent fail — spark là progressive enhancement
  }
}
