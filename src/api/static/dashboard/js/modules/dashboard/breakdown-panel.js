/**
 * breakdown-panel.js
 * Owner: modules/dashboard
 *
 * Slide-in drawer that shows thesis health detail when a heatmap cell is clicked.
 *
 * Public API:
 *   openBreakdownPanel(thesis)  — opens/refreshes the panel for the given thesis
 *   closeBreakdownPanel()       — closes the panel
 *
 * Data fetched:
 *   GET /api/v1/thesis/{id}               → assumptions[], catalysts[], score_breakdown
 *   GET /api/v1/thesis/{id}/reviews/latest → verdict, confidence, risk_signals, next_watch_items
 *
 * Both calls are parallel (Promise.all). Latest review 404 is handled gracefully.
 */

import { thesisApiBase } from '../../api/client.js';

const PANEL_ID = 'bd-panel';

// Status chip config
const ASSUMPTION_STATUS = {
  valid:     { label: 'Valid',     cls: 'chip--green'  },
  invalid:   { label: 'Invalid',  cls: 'chip--red'    },
  pending:   { label: 'Pending',  cls: 'chip--gray'   },
  uncertain: { label: 'Uncertain',cls: 'chip--yellow' },
};

const CATALYST_STATUS = {
  pending:   { label: 'Pending',  cls: 'chip--gray'   },
  triggered: { label: 'Triggered',cls: 'chip--green'  },
  expired:   { label: 'Expired',  cls: 'chip--red'    },
};

const VERDICT_CLS = {
  BULLISH:   'verdict--bull',
  BEARISH:   'verdict--bear',
  NEUTRAL:   'verdict--neutral',
  WATCHLIST: 'verdict--watch',
};

function _getOrCreatePanel() {
  let panel = document.getElementById(PANEL_ID);
  if (!panel) {
    panel = document.createElement('div');
    panel.id = PANEL_ID;
    panel.className = 'bd-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');

    const backdrop = document.createElement('div');
    backdrop.className = 'bd-backdrop';
    backdrop.addEventListener('click', closeBreakdownPanel);

    document.body.appendChild(backdrop);
    document.body.appendChild(panel);

    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') closeBreakdownPanel();
    });
  }
  return panel;
}

function _backdrop() {
  return document.querySelector('.bd-backdrop');
}

export function closeBreakdownPanel() {
  const panel = document.getElementById(PANEL_ID);
  if (panel) panel.classList.remove('bd-panel--open');
  const bd = _backdrop();
  if (bd) bd.classList.remove('bd-backdrop--visible');
}

function _scoreBar(breakdown) {
  if (!breakdown) return '';
  const dims = [
    { key: 'assumption_health',  max: 40, label: 'Assumptions' },
    { key: 'catalyst_progress',  max: 30, label: 'Catalysts'   },
    { key: 'risk_reward',        max: 20, label: 'R/R'         },
    { key: 'review_confidence',  max: 10, label: 'Review'      },
  ];
  return dims.map(d => {
    const val   = breakdown[d.key] ?? 0;
    const pct   = Math.round((val / d.max) * 100);
    const cls   = pct >= 70 ? 'bar--green' : pct >= 40 ? 'bar--yellow' : 'bar--red';
    return `
      <div class="bd-score-row">
        <span class="bd-score-label">${d.label}</span>
        <div class="bd-score-track">
          <div class="bd-score-fill ${cls}" style="width:${pct}%"></div>
        </div>
        <span class="bd-score-pct">${pct}%</span>
      </div>`;
  }).join('');
}

function _chip(statusMap, status) {
  const cfg = statusMap[status?.toLowerCase()] || { label: status || '—', cls: 'chip--gray' };
  return `<span class="bd-chip ${cfg.cls}">${cfg.label}</span>`;
}

function _assumptionList(assumptions) {
  if (!assumptions?.length) return '<p class="bd-empty">No assumptions recorded.</p>';
  return assumptions.map(a => `
    <div class="bd-item">
      <div class="bd-item-row">
        ${_chip(ASSUMPTION_STATUS, a.status)}
        <span class="bd-item-desc">${_esc(a.description)}</span>
      </div>
      ${a.note ? `<div class="bd-item-note">${_esc(a.note)}</div>` : ''}
    </div>`).join('');
}

function _catalystList(catalysts) {
  if (!catalysts?.length) return '<p class="bd-empty">No catalysts recorded.</p>';
  return catalysts.map(c => `
    <div class="bd-item">
      <div class="bd-item-row">
        ${_chip(CATALYST_STATUS, c.status)}
        <span class="bd-item-desc">${_esc(c.description)}</span>
      </div>
      ${c.triggered_at ? `<div class="bd-item-note">Triggered: ${_date(c.triggered_at)}</div>` : ''}
      ${c.expected_date && !c.triggered_at ? `<div class="bd-item-note">Expected: ${_date(c.expected_date)}</div>` : ''}
      ${c.note ? `<div class="bd-item-note">${_esc(c.note)}</div>` : ''}
    </div>`).join('');
}

function _reviewSection(review) {
  if (!review) return '<p class="bd-empty">No AI review yet.</p>';
  const verdictCls = VERDICT_CLS[review.verdict] || 'verdict--neutral';
  const riskItems  = (review.risk_signals || []).map(r => `<li>${_esc(r)}</li>`).join('');
  const watchItems = (review.next_watch_items || []).map(w => `<li>${_esc(w)}</li>`).join('');
  const conf       = review.confidence != null ? Math.round(review.confidence * 100) + '%' : '—';

  return `
    <div class="bd-review">
      <div class="bd-review-header">
        <span class="bd-verdict ${verdictCls}">${review.verdict}</span>
        <span class="bd-review-conf">Confidence: ${conf}</span>
        <span class="bd-review-date">${_date(review.reviewed_at)}</span>
      </div>
      ${review.reasoning ? `<p class="bd-review-reasoning">${_esc(review.reasoning)}</p>` : ''}
      ${riskItems  ? `<div class="bd-subhead">⚠ Risk signals</div><ul class="bd-list">${riskItems}</ul>`   : ''}
      ${watchItems ? `<div class="bd-subhead">👁 Watch items</div><ul class="bd-list">${watchItems}</ul>` : ''}
    </div>`;
}

function _esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function _date(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleDateString('vi-VN'); } catch { return iso; }
}

function _renderPanel(panel, thesis, detail, review) {
  const bd   = detail?.score_breakdown;
  const total = detail?.score != null ? Math.round(detail.score) : '—';
  const tier  = detail?.score_tier || '';

  panel.innerHTML = `
    <div class="bd-header">
      <div class="bd-title">
        <span class="bd-ticker">${_esc(thesis.ticker)}</span>
        <span class="bd-tier">${_esc(tier)}</span>
        <span class="bd-total-score">Score: ${total}</span>
      </div>
      <button class="bd-close" aria-label="Close" id="bd-close-btn">✕</button>
    </div>

    <div class="bd-section">
      <div class="bd-section-title">Health breakdown</div>
      ${_scoreBar(bd)}
    </div>

    <div class="bd-section">
      <div class="bd-section-title">Assumptions (${detail?.assumptions?.length ?? 0})</div>
      ${_assumptionList(detail?.assumptions)}
    </div>

    <div class="bd-section">
      <div class="bd-section-title">Catalysts (${detail?.catalysts?.length ?? 0})</div>
      ${_catalystList(detail?.catalysts)}
    </div>

    <div class="bd-section">
      <div class="bd-section-title">Latest AI review</div>
      ${_reviewSection(review)}
    </div>`;

  // Wire close button after innerHTML set
  panel.querySelector('#bd-close-btn')?.addEventListener('click', closeBreakdownPanel);
}

export async function openBreakdownPanel(thesis) {
  const panel = _getOrCreatePanel();
  const base  = thesisApiBase(); // e.g. /api/v1/thesis

  // Show loading state immediately
  panel.innerHTML = `<div class="bd-loading">Loading breakdown…</div>`;
  panel.classList.add('bd-panel--open');
  const bd = _backdrop();
  if (bd) bd.classList.add('bd-backdrop--visible');

  try {
    const [detailRes, reviewRes] = await Promise.all([
      fetch(`${base}/${thesis.id}`),
      fetch(`${base}/${thesis.id}/reviews/latest`),
    ]);

    const detail = detailRes.ok ? await detailRes.json() : null;
    const review = reviewRes.ok ? await reviewRes.json() : null;

    _renderPanel(panel, thesis, detail, review);
  } catch (err) {
    panel.innerHTML = `<div class="bd-error">Failed to load breakdown.<br><small>${_esc(String(err))}</small></div>`;
  }
}
