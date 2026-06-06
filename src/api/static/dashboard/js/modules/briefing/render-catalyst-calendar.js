/**
 * render-catalyst-calendar.js
 * Owner: modules/briefing
 * Responsibility: render catalyst calendar view (grid/list) from API data.
 *
 * UX: default hiển thị INITIAL_VISIBLE event gần nhất (sorted by date ASC,
 * no-date items last). Nút "Xem thêm (N)" toggle show-all / collapse.
 */

import { el } from '../../utils/dom.js';
import { esc, fmtDate } from '../../utils/format.js';

const INITIAL_VISIBLE = 6;

export function renderCatalystCalendar(raw, wrapId = 'catalystList') {
  const wrap = el(wrapId);
  if (!wrap) return;

  const list = Array.isArray(raw) ? raw : (Array.isArray(raw?.items) ? raw.items : []);

  if (!list.length) {
    wrap.innerHTML = `
      <div class="cal-empty">
        <span class="cal-empty-icon">📅</span>
        <p>Không có catalyst nào trong 30 ngày tới.</p>
      </div>`;
    return;
  }

  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const urgent   = [];
  const upcoming = [];

  list.forEach(item => {
    const raw_date = item.expected_date ?? item.expected_at ?? null;
    if (!raw_date) { upcoming.push(item); return; }
    const d = new Date(String(raw_date).length === 10 ? raw_date + 'T00:00' : raw_date);
    d.setHours(0, 0, 0, 0);
    const diffDays = Math.round((d - today) / 86_400_000);
    if (diffDays >= 0 && diffDays <= 3) urgent.push({ ...item, _diffDays: diffDays });
    else upcoming.push(item);
  });

  const renderItem = (item, isUrgent = false) => {
    const raw_date = item.expected_date ?? item.expected_at ?? null;
    const dateStr = raw_date ? fmtDate(raw_date) : null;
    const ticker = item.thesis_ticker ?? item.ticker ?? null;
    const desc = item.description ?? item.name ?? 'Catalyst';
    const daysLeft = item._diffDays;

    const daysHtml = isUrgent && daysLeft != null
      ? `<span class="cal-days-badge ${daysLeft === 0 ? 'cal-today' : 'cal-urgent'}">${daysLeft === 0 ? 'Hôm nay' : `còn ${daysLeft}d`}</span>`
      : '';

    return `
      <div class="cal-item ${isUrgent ? 'cal-item--urgent' : ''}">
        <div class="cal-item-header">
          ${ticker ? `<span class="cal-ticker">${esc(ticker)}</span>` : ''}
          ${daysHtml}
          ${dateStr ? `<span class="cal-date">📅 ${dateStr}</span>` : ''}
        </div>
        <div class="cal-item-desc">${esc(desc)}</div>
      </div>`;
  };

  // --- Build flat ordered list: urgent first, then upcoming ---
  // urgent items are already few (≤3d); "Xem thêm" applies to upcoming only.
  const upcomingTotal = upcoming.length;
  const hasMore = upcomingTotal > INITIAL_VISIBLE;
  const upcomingVisible = hasMore ? upcoming.slice(0, INITIAL_VISIBLE) : upcoming;
  const upcomingHidden  = hasMore ? upcoming.slice(INITIAL_VISIBLE)    : [];

  let html = '';

  if (urgent.length) {
    html += `<div class="cal-section-label cal-urgent-label">⚡ Sắp đến (≤ 3 ngày)</div>`;
    html += urgent.map(i => renderItem(i, true)).join('');
  }

  if (upcomingTotal) {
    if (urgent.length) html += `<div class="cal-section-label">📆 Sắp tới</div>`;
    html += upcomingVisible.map(i => renderItem(i, false)).join('');
  }

  if (hasMore) {
    html += `
      <div id="cal-hidden-items" style="display:none;">
        ${upcomingHidden.map(i => renderItem(i, false)).join('')}
      </div>
      <button
        id="cal-show-more-btn"
        class="cal-show-more-btn"
        aria-expanded="false"
        style="
          display:block; width:100%; margin-top:8px; padding:6px 0;
          background:none; border:1px solid var(--border,#333);
          border-radius:6px; color:var(--muted,#aaa); font-size:.82rem;
          cursor:pointer; transition:color .15s,border-color .15s;
        "
      >
        Xem thêm (${upcomingHidden.length})
      </button>`;
  }

  wrap.innerHTML = html;

  if (hasMore) {
    const btn     = wrap.querySelector('#cal-show-more-btn');
    const hiddenEl = wrap.querySelector('#cal-hidden-items');
    btn?.addEventListener('click', () => {
      const expanded = btn.getAttribute('aria-expanded') === 'true';
      if (expanded) {
        hiddenEl.style.display = 'none';
        btn.setAttribute('aria-expanded', 'false');
        btn.textContent = `Xem thêm (${upcomingHidden.length})`;
      } else {
        hiddenEl.style.display = 'block';
        btn.setAttribute('aria-expanded', 'true');
        btn.textContent = 'Thu gọn';
      }
    });
  }
}
