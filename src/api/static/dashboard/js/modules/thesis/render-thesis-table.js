import { esc, fmtDate, fmtScore, badge, fmt, scoreClass } from '../../utils/format.js';
import { renderScoreBreakdown } from './render-score.js';
import { renderReviewRecommendSection } from './render-ai-review.js';
import { convictionTimelineSlotHTML } from './render-conviction-timeline.js';
import { quoteStripSkeletonHTML } from './market-quote.js';
import { state } from '../../state/dashboard-state.js';

/**
 * HTML khi chưa chọn thesis nào.
 */
export function emptyDetailHTML() {
  return `<div class="empty-detail"><div class="empty-detail-copy"><h3>Chọn một thesis</h3><p>Xem assumptions, catalysts và review history.</p></div></div>`;
}

/**
 * WAVE 2d — skeleton cho danh sách thesis table.
 * Hiển thị N hàng placeholder trước khi data về.
 * @param {number} rows  số hàng skeleton (default 5)
 */
export function thesisTableSkeletonHTML(rows = 5) {
  const cols = 6;
  const headerCells = Array.from({ length: cols }, () =>
    `<th><div class="skel skel-text" style="width:${30 + Math.random() * 40 | 0}%;"></div></th>`
  ).join('');
  const bodyRows = Array.from({ length: rows }, () => {
    const cells = Array.from({ length: cols }, (_, i) => {
      if (i === cols - 1) return `<td><div class="skel skel-badge" style="width:64px;"></div></td>`;
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
 * Render toàn bộ detail panel cho một thesis.
 * WAVE 3b: thêm quote-strip-placeholder ngay sau detail-head.
 *   - Placeholder (skeleton) được render ngay lập tức (số 0 đồng bộ).
 *   - thesis-service.js sẽ fetch quote song song rồi swap innerHTML của #quoteStripSlot.
 */
export function renderThesisDetailHTML(t, assumptions, catalysts, reviews) {
  const assumList = Array.isArray(assumptions) ? assumptions : (assumptions?.items ?? []);
  const catList   = Array.isArray(catalysts)   ? catalysts   : (catalysts?.items ?? []);
  const revList   = Array.isArray(reviews)     ? reviews     : (reviews?.items ?? []);

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

    ${t.summary ? `<p class="detail-summary">${esc(t.summary)}</p>` : ''}

    <div class="detail-grid">
      <div class="detail-stat"><span>Score</span><strong class="${scoreClass(t.score)}">${fmtScore(t.score)}/100</strong>${t.score_tier ? `<span style="color:var(--muted);font-size:.82rem;">${esc(t.score_tier_icon ?? '')} ${esc(t.score_tier)}</span>` : ''}</div>
      <div class="detail-stat"><span>Entry</span><strong>${t.entry_price ? fmt(t.entry_price) + '₫' : '—'}</strong></div>
      <div class="detail-stat"><span>Target</span><strong>${t.target_price ? fmt(t.target_price) + '₫' : '—'}</strong></div>
      <div class="detail-stat"><span>Stop loss</span><strong>${t.stop_loss ? fmt(t.stop_loss) + '₫' : '—'}</strong></div>
      <div class="detail-stat"><span>Tạo lúc</span><strong style="font-size:.9rem;">${fmtDate(t.created_at)}</strong></div>
      <div class="detail-stat"><span>Cập nhật</span><strong style="font-size:.9rem;">${fmtDate(t.updated_at)}</strong></div>
    </div>

    <div class="detail-columns">
      <div class="detail-section">
        <div class="detail-section-header">
          <h3>Assumptions (${assumList.length})</h3>
          <button class="ghost-btn" style="min-height:34px;padding:0 12px;font-size:.82rem;" id="addAssumBtn">+ Thêm</button>
        </div>
        <div class="detail-list" id="assumptionList">
          ${assumList.length ? assumList.map(renderAssumItem).join('') : '<p class="empty-state">Chưa có assumption.</p>'}
        </div>
      </div>
      <div class="detail-section">
        <div class="detail-section-header">
          <h3>Catalysts (${catList.length})</h3>
          <button class="ghost-btn" style="min-height:34px;padding:0 12px;font-size:.82rem;" id="addCatBtn">+ Thêm</button>
        </div>
        <div class="detail-list" id="catalystDetailList">
          ${catList.length ? catList.map(renderCatItem).join('') : '<p class="empty-state">Chưa có catalyst.</p>'}
        </div>
      </div>
    </div>

    ${revList.length ? `
      <div style="margin-top:18px;">
        <h3 style="margin-bottom:12px;">Review gần nhất</h3>
        ${revList.slice(0, 3).map(r => `
          <div class="review-card">
            <div class="review-head">
              <span class="review-meta">${fmtDate(r.reviewed_at)}</span>
              ${badge(r.verdict)}
              <span style="color:var(--muted);font-size:.82rem;">Conf: ${r.confidence ?? '—'}</span>
            </div>
            <p class="review-reasoning">${esc(r.reasoning ?? '')}</p>
          </div>`).join('')}
      </div>` : ''}

    ${renderScoreBreakdown(t.score_breakdown)}

    <!-- Conviction Timeline slot — filled async by thesis-service -->
    ${convictionTimelineSlotHTML(t.id)}

    ${renderReviewRecommendSection(t.id)}
  `;
}

export function renderAssumItem(a) {
  return `
    <div class="detail-item" data-assum-id="${a.id}">
      <div class="detail-item-row">
        <span style="font-weight:600;font-size:.9rem;">${esc(a.description)}</span>
        <div class="detail-item-actions">
          ${badge(a.status)}
          <button class="icon-btn edit-assum-btn" data-id="${a.id}" title="Sửa">✏️</button>
          <button class="icon-btn danger delete-assum-btn" data-id="${a.id}" title="Xóa">🗑</button>
        </div>
      </div>
      ${a.rationale ? `<p>${esc(a.rationale)}</p>` : ''}
    </div>`;
}

export function renderCatItem(c) {
  return `
    <div class="detail-item" data-cat-id="${c.id}">
      <div class="detail-item-row">
        <span style="font-weight:600;font-size:.9rem;">${esc(c.description)}</span>
        <div class="detail-item-actions">
          ${badge(c.status)}
          <button class="icon-btn edit-cat-btn" data-id="${c.id}" title="Sửa">✏️</button>
          <button class="icon-btn danger delete-cat-btn" data-id="${c.id}" title="Xóa">🗑</button>
        </div>
      </div>
      ${c.expected_timeline ? `<p>📅 ${esc(c.expected_timeline)}</p>` : ''}
      ${c.rationale ? `<p>${esc(c.rationale)}</p>` : ''}
    </div>`;
}

/**
 * Render bảng danh sách theses.
 * @param {Array}  list
 * @param {Object} callbacks  - { onSelect, onEdit, onDelete } — tất cả optional
 *
 * FIX: inline default trong destructure để tránh crash khi callbacks bị undefined
 * (ví dụ do module cache cũ hoặc caller không truyền arg)
 */
export function renderThesesTable(list, callbacks = {}) {
  const { onSelect = null, onEdit = null, onDelete = null } = callbacks ?? {};
  const wrap = document.getElementById('thesesTableWrap');
  if (!list.length) {
    wrap.innerHTML = '<p class="empty-state">Chưa có thesis nào. Nhấn <strong>+ Thesis mới</strong> để tạo.</p>';
    return;
  }

  wrap.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Mã / Hướng</th><th>Tiêu đề</th><th>Score</th>
          <th>Status</th><th>Cập nhật</th><th></th>
        </tr>
      </thead>
      <tbody>
        ${list.map(t => `
          <tr data-id="${t.id}" class="${t.id === state.selectedThesisId ? 'is-selected' : ''}">
            <td class="ticker-cell">
              <strong>${esc(t.ticker)}</strong>
              ${t.direction ? `<span>${badge(t.direction)}</span>` : ''}
            </td>
            <td>${esc(t.title ?? '—')}</td>
            <td class="${scoreClass(t.score)}">
              <div style="display:flex;flex-direction:column;gap:2px;">
                <strong>${fmtScore(t.score)}</strong>
                ${(t.score_tier || t.score_tier_icon) ? `<span style="font-size:.78rem;color:var(--muted);">${esc(t.score_tier_icon ?? '')} ${esc(t.score_tier ?? '')}</span>` : ''}
              </div>
            </td>
            <td>${badge(t.status)}</td>
            <td style="color:var(--muted);font-size:.82rem;">${fmtDate(t.updated_at)}</td>
            <td>
              <div style="display:flex;gap:6px;">
                <button class="icon-btn edit-thesis-btn" data-id="${t.id}" title="Sửa thesis">✏️</button>
                <button class="icon-btn danger delete-thesis-btn" data-id="${t.id}" title="Xóa thesis">🗑</button>
              </div>
            </td>
          </tr>`).join('')}
      </tbody>
    </table>`;

  wrap.querySelectorAll('tbody tr').forEach(row => {
    row.addEventListener('click', e => {
      if (e.target.closest('.edit-thesis-btn') || e.target.closest('.delete-thesis-btn')) return;
      const id = row.dataset.id;
      state.selectedThesisId = id;
      wrap.querySelectorAll('tbody tr').forEach(r => r.classList.toggle('is-selected', r.dataset.id === id));
      onSelect?.(id);
    });
  });
  wrap.querySelectorAll('.edit-thesis-btn').forEach(btn =>
    btn.addEventListener('click', e => { e.stopPropagation(); onEdit?.(btn.dataset.id); })
  );
  wrap.querySelectorAll('.delete-thesis-btn').forEach(btn =>
    btn.addEventListener('click', e => { e.stopPropagation(); onDelete?.(btn.dataset.id); })
  );
}
