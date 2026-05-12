import { esc, fmtDate, fmtScore, badge, fmt, scoreClass } from '../../utils/format.js';
import { renderScoreBreakdown } from './render-score.js';
import { renderReviewRecommendSection } from './render-ai-review.js';
import { convictionTimelineSlotHTML, loadSparkChart, destroySpark } from './render-conviction-timeline.js';
import { quoteStripSkeletonHTML } from './market-quote.js';
import { priceMiniChartSlotHTML } from './render-price-chart.js';
import { state } from '../../state/dashboard-state.js';

/**
 * HTML khi chưa chọn thesis nào.
 */
export function emptyDetailHTML() {
  return `<div class="empty-detail"><div class="empty-detail-copy"><h3>Chọn một thesis</h3><p>Xem assumptions, catalysts và review history.</p></div></div>`;
}

/**
 * WAVE 2d — skeleton cho danh sách thesis table.
 */
export function thesisTableSkeletonHTML(rows = 5) {
  const cols = 7;
  const headerCells = Array.from({ length: cols }, () =>
    `<th><div class="skel skel-text" style="width:${30 + Math.random() * 40 | 0}%;"></div></th>`
  ).join('');
  const bodyRows = Array.from({ length: rows }, () => {
    const cells = Array.from({ length: cols }, (_, i) => {
      if (i === cols - 1) return `<td><div class="skel skel-badge" style="width:64px;"></div></td>`;
      if (i === 3) return `<td><div class="skel" style="width:80px;height:36px;border-radius:4px;"></div></td>`;
      const w = [48, 72, 36, 52, 60, 40][i] ?? 50;
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

/**
 * Wave C: slot HTML cho thesis event timeline.
 * Render placeholder skeleton ngay lập tức;
 * thesis-service.js swap nội dung sau khi fetch xong.
 */
function thesisTimelineSlotHTML(thesisId) {
  return `
    <div id="thesisTimelineSlot-${thesisId}" class="tl-slot" aria-live="polite">
      <div class="tl-section">
        <div class="tl-section-title">📅 Lịch sử thesis</div>
        <div class="tl-skeleton">
          <div class="skel skel-text" style="width:55%;"></div>
          <div class="skel skel-text" style="width:40%;"></div>
          <div class="skel skel-text" style="width:65%;"></div>
        </div>
      </div>
    </div>`;
}

/**
 * Render toàn bộ detail panel cho một thesis.
 * WAVE 3b: quote-strip-placeholder async.
 * Wave C:  thesisTimelineSlot async (sau conviction timeline).
 * Wave C+: priceMiniChartSlot async — Chart.js line chart 30d OHLCV
 *          với annotation lines entry / target / stop_loss.
 */
export function renderThesisDetailHTML(t, assumptions, catalysts, reviews) {
  const assumList = Array.isArray(assumptions) ? assumptions : (assumptions?.items ?? []);
  const catList   = Array.isArray(catalysts)   ? catalysts   : (catalysts?.items ?? []);

  return `
    <div class="detail-head">
      <div>
        <div class="detail-meta">
          <span class="badge" style="font-size:.9rem;padding:6px 12px;">${esc(t.ticker)}</span>
          ${t.direction ? badge(t.direction) : ''}
          ${badge(t.status)}
          ${t.score_tier ? `<span class="badge ${scoreClass(t.score)}">${esc(t.score_tier_icon ?? '')} ${esc(t.score_tier)}</span>` : ''}
        </div>
        <h2 style="margin-top:10px;">${esc(t.title ?? '—')}</h2>
      </div>
      <div class="detail-head-actions">
        <button class="ghost-btn" id="detailEditBtn">✏️ Sửa</button>
        <button class="danger-btn" id="detailDeleteBtn">🗑 Xóa thesis</button>
      </div>
    </div>

    <!-- WAVE 3b: quote strip slot — filled async by thesis-service after render -->
    <div id="quoteStripSlot" data-ticker="${esc(t.ticker)}">
      ${quoteStripSkeletonHTML()}
    </div>

    <!-- Wave C+: price mini chart slot — Chart.js 30d OHLCV với entry/target/stop lines -->
    ${priceMiniChartSlotHTML(t.id)}

    ${t.summary ? `<p class="detail-summary">${esc(t.summary)}</p>` : ''}

    <div class="detail-grid">
      <div class="detail-stat"><span>Score</span><strong class="${scoreClass(t.score)}">${fmtScore(t.score)}/100</strong>${t.score_tier ? `<span style="color:var(--muted);font-size:.82rem;">${esc(t.score_tier_icon ?? '')} ${esc(t.score_tier ?? '')}</span>` : ''}</div>
      <div class="detail-stat"><span>Entry</span><strong>${t.entry_price ? fmt(t.entry_price) + '₫' : '—'}</strong></div>
      <div class="detail-stat"><span>Target</span><strong>${t.target_price ? fmt(t.target_price) + '₫' : '—'}</strong></div>
      <div class="detail-stat"><span>Stop loss</span><strong>${t.stop_loss ? fmt(t.stop_loss) + '₫' : '—'}</strong></div>
      <div class="detail-stat"><span>Tạo lúc</span><strong style="font-size:.9rem;">${fmtDate(t.created_at)}</strong></div>
      <div class="detail-stat"><span>Cập nhật</span><strong style="font-size:.9rem;">${fmtDate(t.updated_at)}</strong></div>
    </div>

    ${renderScoreBreakdown(t.score_breakdown)}

    <div class="detail-columns">
      <div class="detail-col">
        <div class="col-header">
          <h3>Assumptions <span class="count-badge">${assumList.length}</span></h3>
          <button class="icon-btn" id="addAssumBtn" title="Thêm assumption">＋</button>
        </div>
        ${assumList.length ? assumList.map(a => `
          <div class="item-row item-row--${a.status?.toLowerCase() ?? 'unknown'}" data-assum-id="${a.id}">
            <div class="item-row-body">
              <span class="item-text">${esc(a.description ?? '—')}</span>
              <span class="badge badge--${a.status?.toLowerCase() ?? 'unknown'}">${esc(a.status ?? '—')}</span>
            </div>
            <div class="item-row-actions">
              <button class="icon-btn edit-assum-btn" data-id="${a.id}" title="Sửa">✏️</button>
              <button class="icon-btn danger delete-assum-btn" data-id="${a.id}" title="Xóa">🗑</button>
            </div>
          </div>`).join('') : '<p class="empty-state">Chưa có assumption nào.</p>'}
      </div>

      <div class="detail-col">
        <div class="col-header">
          <h3>Catalysts <span class="count-badge">${catList.length}</span></h3>
          <button class="icon-btn" id="addCatBtn" title="Thêm catalyst">＋</button>
        </div>
        ${catList.length ? catList.map(c => `
          <div class="item-row item-row--${c.status?.toLowerCase() ?? 'unknown'}" data-cat-id="${c.id}">
            <div class="item-row-body">
              <span class="item-text">${esc(c.description ?? '—')}</span>
              <span class="badge badge--${c.status?.toLowerCase() ?? 'unknown'}">${esc(c.status ?? '—')}</span>
            </div>
            <div class="item-row-actions">
              <button class="icon-btn edit-cat-btn" data-id="${c.id}" title="Sửa">✏️</button>
              <button class="icon-btn danger delete-cat-btn" data-id="${c.id}" title="Xóa">🗑</button>
            </div>
          </div>`).join('') : '<p class="empty-state">Chưa có catalyst nào.</p>'}
      </div>
    </div>

    ${renderReviewRecommendSection(t.id)}

    ${convictionTimelineSlotHTML(t.id)}

    ${thesisTimelineSlotHTML(t.id)}`;
}

/**
 * Render thesis list table (left panel).
 * data-ticker + data-thesis-id on each <tr> enables the lesson→review
 * UI loop: decision-loader dispatches 'decision:lesson-persisted' with
 * a ticker, thesis-service finds the row via [data-ticker] and adds a badge.
 *
 * Spark chart: lazy-loaded per row via IntersectionObserver.
 * destroySpark() called before re-render to prevent Chart.js instance leaks.
 */
export function renderThesesTable(list, callbacks = {}) {
  const { onSelect = null, onEdit = null, onDelete = null } = callbacks ?? {};
  const wrap = document.getElementById('thesesTableWrap');
  if (!list.length) {
    wrap.innerHTML = '<p class="empty-state">Chưa có thesis nào. Nhấn <strong>+ Thesis mới</strong> để tạo.</p>';
    return;
  }

  // Destroy existing spark instances trước khi re-render
  list.forEach(t => destroySpark(t.id));

  wrap.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Mã</th><th>Tiêu đề</th><th>Score</th>
          <th style="width:80px;text-align:center;">Trend</th>
          <th>Status</th><th>Cập nhật</th><th style="width:1%;white-space:nowrap;"></th>
        </tr>
      </thead>
      <tbody>
        ${list.map(t => `
          <tr data-id="${t.id}" data-ticker="${esc(t.ticker)}" data-thesis-id="${t.id}" class="${t.id === state.selectedThesisId ? 'is-selected' : ''}">
            <td class="ticker-cell">
              <div style="display:flex;align-items:center;gap:6px;flex-wrap:nowrap;">
                <strong>${esc(t.ticker)}</strong>
                ${t.direction ? badge(t.direction) : ''}
              </div>
            </td>
            <td>${esc(t.title ?? '—')}</td>
            <td class="${scoreClass(t.score)}">
              <div style="display:flex;flex-direction:column;gap:2px;">
                <strong>${fmtScore(t.score)}</strong>
                ${(t.score_tier || t.score_tier_icon) ? `<span style="font-size:.78rem;color:var(--muted);">${esc(t.score_tier_icon ?? '')} ${esc(t.score_tier ?? '')}</span>` : ''}
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
            <td style="width:1%;white-space:nowrap;">
              <div style="display:flex;gap:6px;">
                <button class="icon-btn edit-thesis-btn" data-id="${t.id}" title="Sửa thesis">✏️</button>
                <button class="icon-btn danger delete-thesis-btn" data-id="${t.id}" title="Xóa thesis">🗑</button>
              </div>
            </td>
          </tr>`).join('')}
      </tbody>
    </table>`;

  // Row click + action buttons
  wrap.querySelectorAll('tbody tr').forEach(row => {
    row.addEventListener('click', e => {
      if (e.target.closest('.edit-thesis-btn') || e.target.closest('.delete-thesis-btn')) return;
      const id = row.dataset.id;
      state.selectedThesisId = id;
      wrap.querySelectorAll('tbody tr').forEach(r => r.classList.toggle('is-selected', r.dataset.id === id));
      onSelect?.(id);
    });
    row.querySelector('.edit-thesis-btn')?.addEventListener('click', e => {
      e.stopPropagation();
      onEdit?.(row.dataset.id);
    });
    row.querySelector('.delete-thesis-btn')?.addEventListener('click', e => {
      e.stopPropagation();
      onDelete?.(row.dataset.id);
    });
  });

  // Lazy-load spark charts khi row visible
  const sparkObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      const canvas = entry.target;
      const id = canvas.dataset.thesisId;
      sparkObserver.unobserve(canvas);
      loadSparkChart(id, canvas);
    });
  }, { rootMargin: '100px' });

  wrap.querySelectorAll('canvas[data-thesis-id]').forEach(c => sparkObserver.observe(c));
}
