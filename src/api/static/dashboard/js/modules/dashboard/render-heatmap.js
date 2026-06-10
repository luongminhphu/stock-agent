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
 *
 * Public API:
 *   renderHealthHeatmap(theses)       — initial render, called by dashboard-loader
 *   refreshHeatmapCell(thesisId)      — re-fetch breakdown + update cells in-place
 *                                       called after breakdown:review-done event
 */

import { thesisApiBase } from '../../api/client.js';
import { openBreakdownPanel } from './breakdown-panel.js';

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
      ? `${d.label}: ${val} / ${d.max} pts (${pct}%) — click to see detail`
      : `${d.label}: no data — click to see detail`;
    // data-thesis-id on each cell enables refreshHeatmapCell() to target them
    return `<div class="hm-cell hm-cell--clickable ${cellClass(ratio)}" title="${tip}" aria-label="${tip}" data-dim="${d.key}" data-thesis-id="${thesis.id}"></div>`;
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

  // Build a lookup map: thesisId -> thesis object (O(1) slot lookup later)
  const thesisMap = new Map(theses.map(t => [String(t.id), t]));

  // Collect all slots in one querySelectorAll — single DOM query instead of N
  const slots = document.querySelectorAll('#thesesTableWrap .hm-slot[data-thesis-id]');
  if (!slots.length) return;

  // Batch all writes into a DocumentFragment per slot; minimise reflow
  // by doing all reads first, then all writes.
  /** @type {Array<{slot: Element, thesis: object}>} */
  const pending = [];
  slots.forEach(slot => {
    const thesis = thesisMap.get(slot.dataset.thesisId);
    if (thesis) pending.push({ slot, thesis });
  });

  // Write phase — all DOM mutations in one pass
  pending.forEach(({ slot, thesis }) => {
    // Idempotent: remove stale heatmap
    const existing = slot.querySelector('.health-heatmap');
    if (existing) existing.remove();

    const frag = document.createDocumentFragment();
    const tmp  = document.createElement('div');
    tmp.innerHTML = buildHeatmapRow(thesis);
    while (tmp.firstChild) frag.appendChild(tmp.firstChild);
    slot.appendChild(frag);

    // Event delegation via slot — single listener per slot (not per cell)
    slot.addEventListener('click', () => openBreakdownPanel(thesis), { once: false });
  });
}

/**
 * refreshHeatmapCell(thesisId)
 *
 * Re-fetches score_breakdown for a single thesis and updates its 4 heatmap
 * cells in-place — no full table re-render, no layout shift.
 *
 * Called by dashboard-loader after 'breakdown:review-done' event fires
 * (dispatched by breakdown-panel.js when AI review completes successfully).
 *
 * Fails silently: if the thesis row is no longer in DOM or fetch fails,
 * nothing breaks — cells just keep their current color until next reload.
 *
 * @param {number|string} thesisId
 */
export async function refreshHeatmapCell(thesisId) {
  const heatmapEl = document.querySelector(`.health-heatmap[data-thesis-id="${thesisId}"]`);
  if (!heatmapEl) return;

  // Flash cells to indicate update in progress
  heatmapEl.classList.add('hm-refreshing');

  try {
    const res = await fetch(`${thesisApiBase()}/${thesisId}`);
    if (!res.ok) return;
    const thesis = await res.json();
    const bd = thesis?.score_breakdown;

    DIMENSIONS.forEach(d => {
      const cell  = heatmapEl.querySelector(`[data-dim="${d.key}"]`);
      if (!cell) return;

      const val   = bd ? bd[d.key] : null;
      const ratio = (val != null && d.max > 0) ? val / d.max : null;
      const pct   = ratio != null ? Math.round(ratio * 100) : null;

      // Swap color class
      cell.classList.remove('hm-cell--green', 'hm-cell--yellow', 'hm-cell--red', 'hm-cell--none');
      cell.classList.add(cellClass(ratio));

      // Update tooltip
      const tip = pct != null
        ? `${d.label}: ${val} / ${d.max} pts (${pct}%) — click to see detail`
        : `${d.label}: no data — click to see detail`;
      cell.title       = tip;
      cell.ariaLabel   = tip;
    });

    // Brief flash animation to signal cells were updated
    heatmapEl.classList.add('hm-refreshed');
    setTimeout(() => heatmapEl.classList.remove('hm-refreshed'), 800);
  } catch {
    // Silent — cell color stays as-is
  } finally {
    heatmapEl.classList.remove('hm-refreshing');
  }
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
    <span class="hm-legend-hint muted">Click cells for detail</span>
  `;
  wrap.insertAdjacentElement('beforebegin', legend);
}
