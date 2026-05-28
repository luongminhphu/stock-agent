/**
 * renderer.js
 * Owner: modules/thesis/conviction-timeline
 * Responsibility: Parse data, build HTML scaffold, render event list + drawer,
 *   wire interactions, expose public API for slot injection.
 *
 * Public API (re-exported via index.js):
 *   convictionTimelineSlotHTML(thesisId)
 *   renderConvictionTimeline(data)
 *   loadConvictionTimeline(thesisId)
 */

import { esc, fmtDate } from '../../../utils/format.js';
import { readmodelApiBase, getJson } from '../../../api/client.js';
import {
  TIER, TREND_META, BD_META, VERDICT_CLS, EVENT_KIND_ICON, tierColor,
} from './constants.js';
import { ensureChartJs, destroyCharts, buildDualChart, hexToRgba } from './chart-utils.js';

// ─────────────────────────────────────────────────────────────────────────────
// Data parser
// ─────────────────────────────────────────────────────────────────────────────

export function parsePoints(points) {
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
// Event list renderer
// FIX (2026-05-12): reverse before map → newest review at top
// ─────────────────────────────────────────────────────────────────────────────

export function renderEventList(events) {
  if (!events.length) return '<p class="cv-empty">Chưa có sự kiện nào.</p>';
  const shown = events.filter(e => e.kind === 'reviewed' || e.verdict != null || e === events[events.length - 1]);
  const list = shown.length ? shown : events.slice(-5);
  return list.slice().reverse().map(e => {
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

export function renderDrawer(e) {
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

export function wireEventList(containerEl, events) {
  const evEls = containerEl.querySelectorAll('.cv-ev');

  evEls.forEach(el => {
    const handler = () => {
      const idx = Number(el.dataset.evIdx);
      const ev = events.find(e => e.idx === idx);
      if (!ev) return;

      const isOpen = el.getAttribute('aria-expanded') === 'true';

      evEls.forEach(e => {
        e.setAttribute('aria-expanded', 'false');
        const d = e.querySelector('.cv-drawer');
        if (d) d.remove();
      });

      if (!isOpen) {
        el.setAttribute('aria-expanded', 'true');
        el.insertAdjacentHTML('beforeend', renderDrawer(ev));
      }
    };

    el.addEventListener('click', handler);
    el.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handler(); } });
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Scaffold HTML builder
// ─────────────────────────────────────────────────────────────────────────────

export function buildScaffold({ data, labels, scores, prices, events }) {
  const tm = TREND_META[data.trend ?? 'insufficient_data'] ?? TREND_META.insufficient_data;
  const delta = data.delta_score ?? null;
  const deltaSign = delta > 0 ? '+' : '';
  const deltaClass = delta > 0 ? 'cv-delta--up' : delta < 0 ? 'cv-delta--down' : '';

  const tierLegend = TIER.map(t =>
    `<div class="cv-tier-pill"><div class="cv-tier-sq" style="background:${t.color}"></div>${t.label}</div>`
  ).join('');

  const shownEvents = events.filter(e => e.kind === 'reviewed' || e.verdict != null || e === events[events.length - 1]);
  const listEvents  = shownEvents.length ? shownEvents : events.slice(-5);

  const entryLabel = data.entry_price
    ? `· Entry: ${Number(data.entry_price).toLocaleString('vi-VN')}₫`
    : '';

  const hasPrices = prices.some(p => p != null);

  return `
    <div class="cv-section detail-section">
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
// Public API
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
      <div class="loading-text" style="padding:24px;">Đang tải conviction timeline…</div>
    </div>`;

  try {
    await ensureChartJs();
    const data = await getJson(`${readmodelApiBase()}/thesis/${thesisId}/conviction-timeline`);

    if (!data || !Array.isArray(data.points) || !data.points.length) {
      slot.innerHTML = `
        <div class="detail-section">
          <div class="detail-section-header"><h3>Conviction Timeline</h3></div>
          <p class="empty-state">Chưa có snapshot nào. Trigger AI review để tạo điểm dữ liệu đầu tiên.</p>
        </div>`;
      return;
    }

    const { labels, scores, prices, events } = parsePoints(data.points);
    slot.innerHTML = buildScaffold({ data, labels, scores, prices, events });

    const canvasEl = slot.querySelector(`#cvChart-${data.ticker}`);
    if (canvasEl) {
      destroyCharts(data.ticker);
      buildDualChart(canvasEl, { labels, scores, prices, events, entryPrice: data.entry_price });
    }

    const listEl = slot.querySelector(`#cvEventList-${data.ticker}`);
    if (listEl && events.length) wireEventList(listEl, events);

  } catch (err) {
    slot.innerHTML = `
      <div class="detail-section">
        <div class="detail-section-header"><h3>Conviction Timeline</h3></div>
        <p class="error-text">Lỗi load timeline: ${esc(err.message)}</p>
      </div>`;
  }
}
