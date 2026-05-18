/**
 * render-catalyst-calendar.js
 * Owner: modules/briefing
 */

import { el }  from '../../utils/dom.js';
import { esc } from '../../utils/format.js';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function parseLocalDate(str) {
  if (!str) return null;
  const normalized = /^\d{4}-\d{2}-\d{2}$/.test(str) ? str + 'T00:00' : str;
  const d = new Date(normalized);
  return isNaN(d.getTime()) ? null : d;
}

function dateKey(d) {
  const y  = d.getFullYear();
  const m  = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${dd}`;
}

function fmtDayLabel(d) {
  return d.toLocaleDateString('vi-VN', { weekday: 'short', day: 'numeric', month: 'numeric' });
}

function urgency(d) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const target = new Date(d);
  target.setHours(0, 0, 0, 0);
  const diffDays = Math.round((target - today) / 86_400_000);
  if (diffDays <= 0) return 'today';
  if (diffDays === 1) return 'tomorrow';
  if (diffDays <= 4) return 'week';
  return 'later';
}

const TYPE_LABELS = {
  earnings:       'KQKD',
  dividend:       'Cổ tức',
  agm:            'ĐHCĐ',
  regulatory:     'Quy định',
  macro:          'Vĩ mô',
  analyst_day:    'Analyst Day',
  product_launch: 'Sản phẩm',
  guidance:       'Guidance',
  merger:         'M&A',
};

function typeBadgeHtml(type) {
  if (!type) return '';
  const label = TYPE_LABELS[String(type).toLowerCase()] ?? String(type);
  return `<span class="cal-type-badge">${esc(label)}</span>`;
}

const URGENCY_META = {
  today:    { cls: 'cal-urgency--today',    label: 'Hôm nay'  },
  tomorrow: { cls: 'cal-urgency--tomorrow', label: 'Ngày mai' },
  week:     { cls: 'cal-urgency--week',     label: 'Tuần này' },
  later:    { cls: 'cal-urgency--later',    label: 'Sắp tới'  },
};

function groupByDate(list) {
  const groups = {};
  for (const item of list) {
    const d = parseLocalDate(item.expected_date);
    if (!d) continue;
    const key = dateKey(d);
    if (!groups[key]) groups[key] = { date: d, key, items: [] };
    groups[key].items.push(item);
  }
  return Object.values(groups).sort((a, b) => a.date - b.date);
}

function catalystPillHtml(item) {
  const ticker    = item.thesis_ticker ?? null;
  const desc      = item.description   ?? '—';
  const title     = item.thesis_title  ?? null;
  const type      = item.catalyst_type ?? null;
  const confirmed = String(item.status ?? '').toLowerCase() === 'confirmed';

  return `
    <div class="cal-pill ${confirmed ? 'cal-pill--confirmed' : ''}" title="${esc(desc)}">
      <div class="cal-pill-top">
        ${ticker ? `<span class="cal-ticker">${esc(ticker)}</span>` : ''}
        ${typeBadgeHtml(type)}
        ${confirmed ? '<span class="cal-confirmed-dot" title="Đã xác nhận"></span>' : ''}
      </div>
      <div class="cal-pill-desc">${esc(desc)}</div>
      ${title ? `<div class="cal-pill-thesis">${esc(title)}</div>` : ''}
    </div>`;
}

function dayTrackHtml(group) {
  const u      = urgency(group.date);
  const umeta  = URGENCY_META[u];
  const dayLbl = fmtDayLabel(group.date);

  return `
    <div class="cal-day-track cal-day-track--${u}">
      <div class="cal-day-header">
        <span class="cal-day-label">${esc(dayLbl)}</span>
        <span class="cal-urgency-pill ${umeta.cls}">${umeta.label}</span>
        <span class="cal-day-count">${group.items.length}</span>
      </div>
      <div class="cal-pills">
        ${group.items.map(catalystPillHtml).join('')}
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------

export function renderCatalystCalendar(raw) {
  const wrap = el('catalystList');
  if (!wrap) return;

  const list = Array.isArray(raw)
    ? raw
    : Array.isArray(raw?.items) ? raw.items : [];

  const withDate    = list.filter(i => i.expected_date);
  const withoutDate = list.filter(i => !i.expected_date);

  if (!list.length) {
    wrap.innerHTML = `
      <div class="cal-empty">
        <span class="cal-empty-icon">📅</span>
        <p>Không có catalyst nào trong 7 ngày tới.</p>
      </div>`;
    return;
  }

  const tracksHtml = groupByDate(withDate).map(dayTrackHtml).join('');

  const noDateHtml = withoutDate.length
    ? `<div class="cal-day-track cal-day-track--later">
         <div class="cal-day-header">
           <span class="cal-day-label">Chưa xác định ngày</span>
           <span class="cal-day-count">${withoutDate.length}</span>
         </div>
         <div class="cal-pills">
           ${withoutDate.map(catalystPillHtml).join('')}
         </div>
       </div>`
    : '';

  wrap.innerHTML = `
    <div class="cal-timeline">
      <div class="cal-legend">
        <span class="cal-urgency-pill cal-urgency--today">Hôm nay</span>
        <span class="cal-urgency-pill cal-urgency--tomorrow">Ngày mai</span>
        <span class="cal-urgency-pill cal-urgency--week">Tuần này</span>
        <span class="cal-urgency-pill cal-urgency--later">Sắp tới</span>
      </div>
      ${tracksHtml}
      ${noDateHtml}
    </div>`;
}
