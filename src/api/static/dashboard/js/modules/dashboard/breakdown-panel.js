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
 * W3: 'Trigger AI Review' button in panel header.
 *   POST /api/v1/thesis/{id}/review → re-renders review section inline.
 *   Dispatches 'breakdown:review-done' { thesisId } for heatmap refresh.
 *
 * W4: Persist scroll position per thesis.
 *   _scrollCache Map stores scrollTop keyed by thesis.id.
 *   Saved before re-render, restored after. Reset to 0 on different thesis.
 *   Cache cleared on panel close.
 */

import { thesisApiBase } from '../../api/client.js';

const PANEL_ID = 'bd-panel';

// W4: scroll position cache, keyed by thesis.id
const _scrollCache = new Map();
// Track which thesis is currently open
let _openThesisId = null;

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

    // W4: save scroll position continuously while user scrolls
    panel.addEventListener('scroll', () => {
      if (_openThesisId != null) {
        _scrollCache.set(_openThesisId, panel.scrollTop);
      }
    }, { passive: true });
  }
  return panel;
}

function _backdrop() {
  return document.querySelector('.bd-backdrop');
}

export function closeBreakdownPanel() {
  const panel = document.getElementById(PANEL_ID);
  if (panel) {
    // W4: save final scroll before closing, then clear cache
    if (_openThesisId != null) {
      _scrollCache.set(_openThesisId, panel.scrollTop);
    }
    panel.classList.remove('bd-panel--open');
  }
  const bd = _backdrop();
  if (bd) bd.classList.remove('bd-backdrop--visible');
  // Clear cache on close — stale scroll positions shouldn't survive a close
  _scrollCache.clear();
  _openThesisId = null;
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
    const val = breakdown[d.key] ?? 0;
    const pct = Math.round((val / d.max) * 100);
    const cls = pct >= 70 ? 'bar--green' : pct >= 40 ? 'bar--yellow' : 'bar--red';
    return `
      <div class="bd-score-row">
        <span class="bd-score-label">${d.label}</span>
        <div class="bd-score-track"><div class="bd-score-fill ${cls}" style="width:${pct}%"></div></div>
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

function _reviewSectionHTML(review) {
  if (!review) return '<p class="bd-empty">No AI review yet.</p>';
  const verdictCls = VERDICT_CLS[review.verdict] || 'verdict--neutral';
  const riskItems  = (review.risk_signals || []).map(r => `<li>${_esc(r)}</li>`).join('');
  const watchItems = (review.next_watch_items || []).map(w => `<li>${_esc(w)}</li>`).join('');
  const conf       = review.confidence != null ? Math.round(review.confidence * 100) + '%' : '—';
  return `
    <div class="bd-review">
      <div class="bd-review-header">
        <span class="bd-verdict ${verdictCls}">${_esc(review.verdict)}</span>
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

// ---------------------------------------------------------------------------
// W3: Trigger AI review from within the panel
// ---------------------------------------------------------------------------
async function _triggerReviewInPanel(thesis, panel) {
  const btn         = panel.querySelector('#bd-review-btn');
  const reviewTitle = panel.querySelector('#bd-review-section-title');
  const reviewBody  = panel.querySelector('#bd-review-body');
  if (!btn || !reviewBody) return;

  btn.disabled  = true;
  btn.innerHTML = '<span class="bd-spinner"></span> Running…';
  if (reviewTitle) reviewTitle.textContent = 'Latest AI review — running…';
  reviewBody.innerHTML = '<p class="bd-empty">AI đang phân tích thesis…</p>';

  // W4: save scroll before async op so position isn't lost
  if (_openThesisId != null) _scrollCache.set(_openThesisId, panel.scrollTop);

  try {
    const base = thesisApiBase();
    const res  = await fetch(`${base}/${thesis.id}/review`, { method: 'POST' });

    if (!res.ok) {
      const msg = await res.text().catch(() => res.statusText);
      throw new Error(`${res.status} ${msg}`);
    }

    const data = await res.json();
    reviewBody.innerHTML = _reviewSectionHTML(data);
    if (reviewTitle) reviewTitle.textContent = 'Latest AI review';

    btn.innerHTML = '✓ Done';
    btn.classList.add('bd-review-btn--done');
    setTimeout(() => {
      btn.innerHTML = '🧠 AI Review';
      btn.classList.remove('bd-review-btn--done');
      btn.disabled = false;
    }, 2500);

    document.dispatchEvent(new CustomEvent('breakdown:review-done', {
      detail: { thesisId: thesis.id },
    }));

  } catch (err) {
    reviewBody.innerHTML = `<div class="bd-error">Review lỗi: ${_esc(err.message)}</div>`;
    if (reviewTitle) reviewTitle.textContent = 'Latest AI review';
    btn.disabled  = false;
    btn.innerHTML = '🧠 AI Review';
  }
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------
function _renderPanel(panel, thesis, detail, review) {
  const bd    = detail?.score_breakdown;
  const total = detail?.score != null ? Math.round(detail.score) : '—';
  const tier  = detail?.score_tier || '';

  // W4: capture scroll before wiping innerHTML
  const isSameThesis = _openThesisId === thesis.id;
  const savedScroll  = isSameThesis ? (_scrollCache.get(thesis.id) ?? 0) : 0;

  panel.innerHTML = `
    <div class="bd-header">
      <div class="bd-title">
        <span class="bd-ticker">${_esc(thesis.ticker)}</span>
        <span class="bd-tier">${_esc(tier)}</span>
        <span class="bd-total-score">Score: ${total}</span>
      </div>
      <div class="bd-header-actions">
        <button class="bd-review-btn" id="bd-review-btn" title="Trigger AI review for this thesis">
          🧠 AI Review
        </button>
        <button class="bd-close" id="bd-close-btn" aria-label="Close">✕</button>
      </div>
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
      <div class="bd-section-title" id="bd-review-section-title">Latest AI review</div>
      <div id="bd-review-body">${_reviewSectionHTML(review)}</div>
    </div>`;

  // W4: restore scroll after DOM is ready
  // requestAnimationFrame ensures layout is complete before scrolling
  requestAnimationFrame(() => { panel.scrollTop = savedScroll; });

  panel.querySelector('#bd-close-btn')?.addEventListener('click', closeBreakdownPanel);
  panel.querySelector('#bd-review-btn')?.addEventListener('click', () => _triggerReviewInPanel(thesis, panel));
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------
export async function openBreakdownPanel(thesis) {
  const panel = _getOrCreatePanel();
  const base  = thesisApiBase();

  // W4: save scroll of currently open thesis before switching
  if (_openThesisId != null && _openThesisId !== thesis.id) {
    _scrollCache.set(_openThesisId, panel.scrollTop);
  }

  // Update open thesis tracking
  _openThesisId = thesis.id;

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
