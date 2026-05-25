import { esc, fmtDate, fmtScore, badge, fmt, scoreClass } from '../../utils/format.js';
import { renderScoreBreakdown } from './render-score.js';
import { renderReviewRecommendSection } from './render-ai-review.js';
import { convictionTimelineSlotHTML, loadSparkChart, destroySpark } from './conviction-timeline/index.js';
import { quoteStripSkeletonHTML } from './market-quote.js';
import { priceMiniChartSlotHTML } from './render-price-chart.js';
import { state } from '../../state/dashboard-state.js';

export function emptyDetailHTML() {
  return `<div class="empty-detail"><div class="empty-detail-copy"><h3>Chọn một thesis</h3><p>Xem assumptions, catalysts và review history.</p></div></div>`;
}

export function thesisTableSkeletonHTML(rows = 5) {
  const cols = 8;
  const headerCells = Array.from({ length: cols }, () =>
    `<th><div class="skel skel-text" style="width:${30 + Math.random() * 40 | 0}%;"></div></th>`
  ).join('');
  const bodyRows = Array.from({ length: rows }, () => {
    const cells = Array.from({ length: cols }, (_, i) => {
      if (i === cols - 1) return `<td><div class="skel skel-badge" style="width:64px;"></div></td>`;
      if (i === 1) return `<td><div class="skel skel-badge" style="width:56px;"></div></td>`;
      if (i === 4) return `<td><div class="skel" style="width:80px;height:36px;border-radius:4px;"></div></td>`;
      const w = [48, 40, 72, 36, 52, 60, 40][i] ?? 50;
      return `<td><div class="skel skel-text" style="width:${w}%;"></div></td>`;
    }).join('');
    return `<tr style="pointer-events:none;">${cells}</tr>`;
  }).join('');
  return `
    <div class="skel-table-wrap" aria-busy="true" aria-label="Đang tải danh sách thesis…">
      <table>
        <thead><tr>${headerCells}</tr></thead>
        <tbody>${bodyRows}</tbody>
      </table>
    </div>`;
}

function thesisTimelineSlotHTML(thesisId) {
  return `
    <div id="thesisTimelineSlot-${thesisId}" class="tl-slot" aria-live="polite">
      <div class="tl-section">
        <div class="tl-section-title">\uD83D\uDCC5 Lịch sử thesis</div>
        <div class="tl-skeleton">
          <div class="skel skel-text" style="width:55%;"></div>
          <div class="skel skel-text" style="width:40%;"></div>
          <div class="skel skel-text" style="width:65%;"></div>
        </div>
      </div>
    </div>`;
}

function calcUpside(entry, target) {
  if (!entry || !target || entry <= 0) return null;
  return ((target - entry) / entry * 100).toFixed(1);
}

export function wireTabNav(wrap) {
  const nav    = wrap.querySelector('.detail-tab-nav');
  const panels = wrap.querySelectorAll('.dtab-panel');
  if (!nav || !panels.length) return;

  nav.addEventListener('click', function(e) {
    const btn = e.target.closest('.dtab');
    if (!btn) return;

    nav.querySelectorAll('.dtab').forEach(b => {
      b.classList.remove('active');
      b.setAttribute('aria-selected', 'false');
    });
    panels.forEach(p => p.classList.remove('active'));

    btn.classList.add('active');
    btn.setAttribute('aria-selected', 'true');

    const target = wrap.querySelector('#dtab-' + btn.dataset.tab);
    if (target) target.classList.add('active');
  });
}

export function renderThesisDetailHTML(t, assumptions, catalysts, reviews) {
  const assumList = Array.isArray(assumptions) ? assumptions : (assumptions?.items ?? []);
  const catList   = Array.isArray(catalysts)   ? catalysts   : (catalysts?.items ?? []);

  const upside = calcUpside(t.entry_price, t.target_price);
  const downsideRisk = t.entry_price && t.stop_loss
    ? ((t.stop_loss - t.entry_price) / t.entry_price * 100).toFixed(1)
    : null;

  const assumInvalid = assumList.filter(a => ['invalid','needs_monitoring'].includes(a.status?.toLowerCase())).length;
  const catPending   = catList.filter(c => c.status?.toLowerCase() === 'pending').length;
  const catExpired   = catList.filter(c => c.status?.toLowerCase() === 'expired').length;

  return `
    <div class="detail-sticky-bar">
      <div class="dsb-left">
        <span class="dsb-ticker">${esc(t.ticker)}</span>
        <div class="dsb-badges">
          ${t.direction ? badge(t.direction) : ''}
          ${badge(t.status)}
          ${t.score_tier ? `<span class="badge ${scoreClass(t.score)}">${esc(t.score_tier_icon ?? '')} ${esc(t.score_tier)}</span>` : ''}
        </div>
      </div>
      <div class="dsb-kpis">
        <div class="dsb-kpi">
          <span class="dsb-kpi-label">Score</span>
          <strong class="dsb-kpi-value ${scoreClass(t.score)}">${fmtScore(t.score)}/100</strong>
        </div>
        ${t.entry_price ? `<div class="dsb-kpi">
          <span class="dsb-kpi-label">Entry</span>
          <strong class="dsb-kpi-value">${fmt(t.entry_price)}\u20ab</strong>
        </div>` : ''}
        ${t.target_price ? `<div class="dsb-kpi dsb-kpi--upside">
          <span class="dsb-kpi-label">Target</span>
          <strong class="dsb-kpi-value">${fmt(t.target_price)}\u20ab ${upside ? `<span class="dsb-upside">+${upside}%</span>` : ''}</strong>
        </div>` : ''}
        ${t.stop_loss ? `<div class="dsb-kpi dsb-kpi--risk">
          <span class="dsb-kpi-label">Stop</span>
          <strong class="dsb-kpi-value">${fmt(t.stop_loss)}\u20ab ${downsideRisk ? `<span class="dsb-downside">${downsideRisk}%</span>` : ''}</strong>
        </div>` : ''}
      </div>
      <div class="dsb-actions">
        <button class="ghost-btn" id="detailEditBtn">\u270f\ufe0f S\u1eeda</button>
        <button class="danger-btn" id="detailDeleteBtn">\uD83D\uDDD1 X\u00f3a</button>
      </div>
    </div>

    <nav class="detail-tab-nav" role="tablist" aria-label="Thesis sections">
      <button class="dtab active" role="tab" aria-selected="true"  data-tab="overview"     aria-controls="dtab-overview">\uD83D\uDCCA Overview</button>
      <button class="dtab"        role="tab" aria-selected="false" data-tab="assumptions"  aria-controls="dtab-assumptions">
        Assumptions <span class="dtab-count ${assumInvalid > 0 ? 'dtab-count--warn' : ''}">${assumList.length}</span>
      </button>
      <button class="dtab"        role="tab" aria-selected="false" data-tab="catalysts"    aria-controls="dtab-catalysts">
        Catalysts <span class="dtab-count ${catExpired > 0 ? 'dtab-count--danger' : catPending > 0 ? 'dtab-count--warn' : ''}">${catList.length}</span>
      </button>
      <button class="dtab"        role="tab" aria-selected="false" data-tab="reviews"      aria-controls="dtab-reviews">\uD83D\uDD0D Reviews</button>
      <button class="dtab"        role="tab" aria-selected="false" data-tab="history"      aria-controls="dtab-history">\uD83D\uDCC5 History</button>
    </nav>

    <div class="detail-tab-panels">

      <!-- OVERVIEW -->
      <div id="dtab-overview" class="dtab-panel active" role="tabpanel">
        <div id="quoteStripSlot" data-ticker="${esc(t.ticker)}">
          ${quoteStripSkeletonHTML()}
        </div>
        ${priceMiniChartSlotHTML(t.id)}
        ${t.summary ? `<p class="detail-summary">${esc(t.summary)}</p>` : ''}
        <div class="overview-meta-grid">
          <div class="meta-stat">
            <span class="meta-stat-label">T\u1ea1o l\u00fac</span>
            <span class="meta-stat-value">${fmtDate(t.created_at)}</span>
          </div>
          <div class="meta-stat">
            <span class="meta-stat-label">C\u1eadp nh\u1eadt</span>
            <span class="meta-stat-value">${fmtDate(t.updated_at)}</span>
          </div>
          <div class="meta-stat">
            <span class="meta-stat-label">Assumptions</span>
            <span class="meta-stat-value">${assumList.length} <span style="color:var(--muted);font-size:.8rem;">(${assumInvalid} c\u1ea7n xem)</span></span>
          </div>
          <div class="meta-stat">
            <span class="meta-stat-label">Catalysts</span>
            <span class="meta-stat-value">${catList.length} <span style="color:var(--muted);font-size:.8rem;">(${catPending} pending)</span></span>
          </div>
        </div>
        ${renderScoreBreakdown(t.score_breakdown)}
      </div>

      <!-- ASSUMPTIONS -->
      <div id="dtab-assumptions" class="dtab-panel" role="tabpanel">
        <div class="tab-panel-toolbar">
          <span class="tab-panel-title">Assumptions <span class="count-badge">${assumList.length}</span></span>
          <button class="primary-btn" id="addAssumBtn" type="button">+ Thêm Assumption</button>
        </div>
        <div class="item-list" id="assumptionList">
          ${assumList.length ? assumList.map(a => `
            <div class="item-card item-card--${a.status?.toLowerCase() ?? 'unknown'}" data-assum-id="${a.id}">
              <div class="item-card-body">
                <span class="item-card-text">${esc(a.description ?? '\u2014')}</span>
                <div class="item-card-meta">
                  <span class="badge badge--${a.status?.toLowerCase() ?? 'unknown'}">${esc(a.status ?? '\u2014')}</span>
                </div>
              </div>
              <div class="item-card-actions">
                <button class="icon-btn edit-assum-btn" data-id="${a.id}" title="S\u1eeda">\u270f\ufe0f</button>
                <button class="icon-btn danger delete-assum-btn" data-id="${a.id}" title="X\u00f3a">\uD83D\uDDD1</button>
              </div>
            </div>`).join('') : '<p class="empty-state">Ch\u01b0a c\u00f3 assumption n\u00e0o.</p>'}
        </div>
      </div>

      <!-- CATALYSTS -->
      <div id="dtab-catalysts" class="dtab-panel" role="tabpanel">
        <div class="tab-panel-toolbar">
          <span class="tab-panel-title">Catalysts <span class="count-badge">${catList.length}</span></span>
          <button class="primary-btn" id="addCatBtn" type="button">+ Thêm Catalyst</button>
        </div>
        <div class="item-list" id="catalystList">
          ${catList.length ? catList.map(c => `
            <div class="item-card item-card--${c.status?.toLowerCase() ?? 'unknown'}" data-cat-id="${c.id}">
              <div class="item-card-body">
                <span class="item-card-text">${esc(c.description ?? '\u2014')}</span>
                <div class="item-card-meta">
                  <span class="badge badge--${c.status?.toLowerCase() ?? 'unknown'}">${esc(c.status ?? '\u2014')}</span>
                  ${c.expected_date ? `<span class="item-card-date">\uD83D\uDCC5 ${fmtDate(c.expected_date)}</span>` : ''}
                </div>
              </div>
              <div class="item-card-actions">
                <button class="icon-btn edit-cat-btn" data-id="${c.id}" title="S\u1eeda">\u270f\ufe0f</button>
                <button class="icon-btn danger delete-cat-btn" data-id="${c.id}" title="X\u00f3a">\uD83D\uDDD1</button>
              </div>
            </div>`).join('') : '<p class="empty-state">Ch\u01b0a c\u00f3 catalyst n\u00e0o.</p>'}
        </div>
      </div>

      <!-- REVIEWS -->
      <div id="dtab-reviews" class="dtab-panel" role="tabpanel">
        ${renderReviewRecommendSection(t.id)}
        <div id="convictionTimelineSlot-${t.id}" class="conviction-slot">
          <div class="skel skel-text" style="width:40%;margin-bottom:6px;"></div>
          <div class="skel skel-text" style="width:60%;"></div>
        </div>
      </div>

      <!-- HISTORY -->
      <div id="dtab-history" class="dtab-panel" role="tabpanel">
        ${thesisTimelineSlotHTML(t.id)}
      </div>

    </div>`;
}

export function detailSkeletonHTML() {
  return `
    <div class="skel-detail-wrap" aria-busy="true" aria-label="\u0110ang t\u1ea3i thesis...">
      <div class="detail-sticky-bar" style="opacity:.5;">
        <div class="dsb-left">
          <div class="skel" style="width:60px;height:24px;border-radius:8px;"></div>
          <div class="skel skel-badge" style="width:70px;"></div>
        </div>
        <div class="dsb-kpis">
          <div class="skel skel-text" style="width:48px;"></div>
          <div class="skel skel-text" style="width:56px;"></div>
          <div class="skel skel-text" style="width:56px;"></div>
        </div>
      </div>
      <div class="detail-tab-nav" style="opacity:.4;">
        ${['Overview','Assumptions','Catalysts','Reviews','History'].map(l => `<button class="dtab">${l}</button>`).join('')}
      </div>
      <div style="padding:20px;display:grid;gap:12px;">
        <div class="skel" style="height:80px;border-radius:12px;"></div>
        <div class="skel" style="height:120px;border-radius:12px;"></div>
        <div class="skel skel-text" style="width:70%;"></div>
        <div class="skel skel-text" style="width:55%;"></div>
      </div>
    </div>`;
}

export function renderThesesTable(list, callbacks = {}) {
  const { onSelect = null, onEdit = null, onDelete = null } = callbacks ?? {};
  const wrap = document.getElementById('thesesTableWrap');
  if (!wrap) {
    console.warn('[render-thesis-table] #thesesTableWrap not found in DOM, skipping render');
    return;
  }
  if (!list.length) {
    wrap.innerHTML = '<p class="empty-state">Ch\u01b0a c\u00f3 thesis n\u00e0o. Nh\u1ea5n <strong>+ Thesis m\u1edbi</strong> \u0111\u1ec3 t\u1ea1o.</p>';
    return;
  }

  list.forEach(t => destroySpark(t.id));

  wrap.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>M\u00e3</th>
          <th style="width:72px;text-align:center;">H\u01b0\u1edbng</th>
          <th>Ti\u00eau \u0111\u1ec1</th>
          <th style="min-width:110px;white-space:nowrap;">Score</th>
          <th style="width:80px;text-align:center;">Trend</th>
          <th>Status</th>
          <th>C\u1eadp nh\u1eadt</th>
          <th style="width:1%;white-space:nowrap;"></th>
        </tr>
      </thead>
      <tbody>
        ${list.map(t => {
          const tier = t.score_tier ?? '';
          const rowClass = [
            t.id === state.selectedThesisId ? 'is-selected' : '',
            tier === 'AT_RISK'  ? 'row--at-risk'  : '',
            tier === 'CRITICAL' ? 'row--critical' : '',
          ].filter(Boolean).join(' ');

          let tierBadge = '';
          if (tier === 'CRITICAL') {
            tierBadge = `<span class="badge score-low" style="font-size:.72rem;">\uD83D\uDD34 CRITICAL</span>`;
          } else if (tier === 'AT_RISK') {
            tierBadge = `<span class="badge score-mid" style="font-size:.72rem;">\u26a0 AT_RISK</span>`;
          } else if (tier || t.score_tier_icon) {
            tierBadge = `<span style="font-size:.78rem;color:var(--muted);">${esc(t.score_tier_icon ?? '')} ${esc(tier)}</span>`;
          }

          return `
          <tr data-id="${t.id}" data-ticker="${esc(t.ticker)}" data-thesis-id="${t.id}" class="${rowClass}">
            <td class="ticker-cell"><strong>${esc(t.ticker)}</strong></td>
            <td style="text-align:center;white-space:nowrap;vertical-align:middle;">
              ${t.direction ? badge(t.direction) : '<span style="color:var(--muted);">\u2014</span>'}
            </td>
            <td>${esc(t.title ?? '\u2014')}</td>
            <td class="${scoreClass(t.score)}">
              <div style="display:flex;flex-direction:row;align-items:center;gap:6px;flex-wrap:wrap;">
                <strong>${fmtScore(t.score)}</strong>
                ${tierBadge}
              </div>
            </td>
            <td style="padding:4px 8px;vertical-align:middle;">
              <canvas
                id="spark-${t.id}"
                width="80"
                height="36"
                data-thesis-id="${t.id}"
                aria-label="Conviction trend for ${esc(t.ticker)}"
                style="display:block;"
              ></canvas>
            </td>
            <td>${badge(t.status)}</td>
            <td style="color:var(--muted);font-size:.82rem;">${fmtDate(t.updated_at)}</td>
            <td style="width:1%;white-space:nowrap;text-align:center;vertical-align:middle;">
              <div style="display:flex;gap:6px;align-items:center;justify-content:center;">
                <button class="icon-btn edit-thesis-btn" data-id="${t.id}" title="S\u1eeda thesis">\u270f\ufe0f</button>
                <button class="icon-btn danger delete-thesis-btn" data-id="${t.id}" title="X\u00f3a thesis">\uD83D\uDDD1</button>
              </div>
            </td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;

  wrap.querySelectorAll('tbody tr').forEach(row => {
    row.addEventListener('click', e => {
      if (e.target.closest('.edit-thesis-btn') || e.target.closest('.delete-thesis-btn')) return;
      const id = row.dataset.id;
      state.selectedThesisId = id;
      if (onSelect) onSelect(id);
      wrap.querySelectorAll('tr').forEach(r => r.classList.remove('is-selected'));
      row.classList.add('is-selected');
    });
    const editBtn = row.querySelector('.edit-thesis-btn');
    const delBtn  = row.querySelector('.delete-thesis-btn');
    if (editBtn && onEdit)   editBtn.addEventListener('click',   () => onEdit(row.dataset.id));
    if (delBtn  && onDelete) delBtn.addEventListener('click',    () => onDelete(row.dataset.id));
  });

  list.forEach(t => {
    const canvas = document.getElementById(`spark-${t.id}`);
    if (canvas) loadSparkChart(t.id, canvas);
  });
}
