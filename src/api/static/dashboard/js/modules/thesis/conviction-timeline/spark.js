/**
 * spark.js
 * Owner: modules/thesis/conviction-timeline
 * Responsibility: Spark (mini inline) chart — used in thesis table rows.
 *   Fetches conviction-timeline and renders a compact Chart.js line.
 *   Designed as progressive enhancement — silent fail, never blocks table render.
 */

import { tierColor, hexToRgba } from './constants.js';
import { ensureChartJs } from './chart-utils.js';
import { thesisApiBase, getJson } from '../../../api/client.js';

const _sparkInstances = new Map();

/**
 * Destroy spark chart instance to prevent memory leaks on table re-render.
 */
export function destroySpark(thesisId) {
  const key = `spark:${thesisId}`;
  if (_sparkInstances.has(key)) { _sparkInstances.get(key).destroy(); _sparkInstances.delete(key); }
}

/**
 * Render spark chart into canvasEl from conviction-timeline points.
 * Used by thesis table row — does NOT need annotation plugin.
 */
export function renderSparkChart(canvasEl, points, thesisId) {
  destroySpark(thesisId);
  if (!points?.length) return;

  const scores = points.map(p => Number(p.score ?? 0));
  const latest = scores[scores.length - 1];
  const color  = tierColor(latest);

  const ctx = canvasEl.getContext('2d');
  const grad = ctx.createLinearGradient(0, 0, 0, 40);
  grad.addColorStop(0, hexToRgba(color, 0.3));
  grad.addColorStop(1, hexToRgba(color, 0));

  const chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: scores.map((_, i) => i),
      datasets: [{
        data: scores,
        borderColor: color,
        backgroundColor: grad,
        borderWidth: 1.5,
        tension: 0.4,
        fill: true,
        pointRadius: 0,
        pointHoverRadius: 3,
      }],
    },
    options: {
      responsive: false,
      animation: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: { display: false },
        y: { display: false, min: 0, max: 100 },
      },
    },
  });

  _sparkInstances.set(`spark:${thesisId}`, chart);
  return chart;
}

/**
 * Fetch conviction-timeline then render spark into canvasEl.
 * Called by IntersectionObserver in render-thesis-table.js.
 * Silent fail — spark is progressive enhancement, does not block table render.
 */
export async function loadSparkChart(thesisId, canvasEl) {
  try {
    await ensureChartJs();
    const data = await getJson(`${thesisApiBase()}/${thesisId}/conviction-timeline`);
    if (!data?.points?.length) return;
    renderSparkChart(canvasEl, data.points, thesisId);
  } catch (_) {
    // silent fail
  }
}
