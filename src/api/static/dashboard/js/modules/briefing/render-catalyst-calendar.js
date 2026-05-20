/**
 * render-catalyst-calendar.js
 * Owner: modules/briefing
 * Responsibility: render catalyst calendar view (grid/list) from API data.
 */

import { el } from '../../utils/dom.js';
import { esc, fmtDate } from '../../utils/format.js';

export function renderCatalystCalendar(raw, wrapId = 'catalystCalendarWrap') {
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
    // Fix: append T00:00 to date-only strings so JS parses in local time (not UTC midnight)
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

  let html = '';

  if (urgent.length) {
    html += `<div class="cal-section-label cal-urgent-label">⚡ Sắp đến (≤ 3 ngày)</div>`;
    html += urgent.map(i => renderItem(i, true)).join('');
  }

  if (upcoming.length) {
    if (urgent.length) html += `<div class="cal-section-label">📆 Sắp tới</div>`;
    html += upcoming.map(i => renderItem(i, false)).join('');
  }

  wrap.innerHTML = html;
}
