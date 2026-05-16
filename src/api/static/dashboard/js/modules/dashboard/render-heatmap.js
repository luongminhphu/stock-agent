/**
 * render-heatmap.js
 * Owner: modules/dashboard
 *
 * Renders a 4-dimension health heatmap for each thesis row,
 * using score_breakdown exposed by the scoring pipeline.
 *
 * Dimensions and max points:
 *   assumption_health   -> max 40
 *   catalyst_progress   -> max 30
 *   risk_reward         -> max 20
 *   review_confidence   -> max 10
 *
 * Color thresholds (ratio = actual / max):
 *   ratio >= 0.70  ->  green  (healthy)
 *   ratio >= 0.40  ->  yellow (weak)
 *   ratio <  0.40  ->  red    (critical)
 *   null / missing ->  gray   (no data)
 */

const DIMENSIONS = [
  { key: 'assumption_health',  max: 40, label: 'Assumptions' },
  { key: 'catalyst_progress',  max: 30, label: 'Catalysts'   },
  { key: 'risk_reward',        max: 20, label: 'R/R'         },
  { key: 'review_confidence',  max: 10, label: 'Review'      },
];

function cellClass(ratio) {
  if (ratio == null) return 'hm-cell--none';
  if (ratio >= 0.70)  return 'hm-cell--green';
  if (ratio >= 0.40)  return 'hm-cell--yellow';
  return 'hm-cell--red';
}

function buildHeatmapRow(thesis) {
  const bd = thesis.score_breakdown;
  const cells = DIMENSIONS.map(d => {
    const val   = bd ? bd[d.key] : null;
    const ratio = (val != null && d.max > 0) ? val / d.max : null;
    const pct   = ratio != null ? Math.round(ratio * 100) : null;
    const tip   = pct != null
      ? `${d.label}: ${val} / ${d.max} pts (${pct}%)`
      : `${d.label}: no data`;
    return `<div class="hm-cell ${cellClass(ratio)}" title="${tip}" aria-label="${tip}"></div>`;
  }).join('');

  return `<div class="health-heatmap" data-thesis-id="${thesis.id}" aria-label="Health breakdown for ${thesis.ticker}">${cells}</div>`;
}

/**
 * renderHealthHeatmap(theses)
 *
 * Injects a .health-heatmap bar into each thesis row in #thesesTableWrap.
 * Safe to call even if score_breakdown is missing — cells render gray.
 * Idempotent: removes existing heatmap before re-injecting.
 *
 * @param {Array} theses - array of thesis objects from get_theses_list()
 */
export function renderHealthHeatmap(theses) {
  if (!theses || !theses.length) return;

  _renderLegend();

  theses.forEach(thesis => {
    const row = document.querySelector(
      `#thesesTableWrap [data-thesis-id="${thesis.id}"]`
    );
    if (!row) return;

    // Idempotent: remove previous render on hot reload
    const existing = row.querySelector('.health-heatmap');
    if (existing) existing.remove();

    row.insertAdjacentHTML('beforeend', buildHeatmapRow(thesis));
  });
}

function _renderLegend() {
  const wrap = document.getElementById('thesesTableWrap');
  if (!wrap) return;

  const legendId = 'healthHeatmapLegend';
  if (document.getElementById(legendId)) return;

  const legend = document.createElement('div');
  legend.id = legendId;
  legend.className = 'hm-legend';
  legend.innerHTML = `
    <span class="hm-legend-label">Health breakdown:</span>
    <span class="hm-legend-item"><span class="hm-cell hm-cell--green hm-cell--sm"></span> &ge;70%</span>
    <span class="hm-legend-item"><span class="hm-cell hm-cell--yellow hm-cell--sm"></span> 40&ndash;69%</span>
    <span class="hm-legend-item"><span class="hm-cell hm-cell--red hm-cell--sm"></span> &lt;40%</span>
    <span class="hm-legend-item"><span class="hm-cell hm-cell--none hm-cell--sm"></span> no data</span>
    <span class="hm-legend-dims muted">A &middot; C &middot; R/R &middot; Rev</span>
  `;
  wrap.insertAdjacentElement('beforebegin', legend);
}
