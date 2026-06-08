/**
 * render-catalyst-calendar.js  — Compact row layout
 * Owner  : modules/briefing
 * HTML   : #catalystList
 * CSS    : css/modules/catalyst-calendar.css (.cc-*)
 *
 * Layout (single row per catalyst):
 *   [urgency badge] [ticker] [desc — truncated] [date]
 *
 * Groups: ⚡ Sắp đến (≤3d) shown first with coloured left accent,
 *         📆 Sắp tới   shown below as plain rows.
 *
 * "Xem thêm" toggle: default shows INITIAL_VISIBLE upcoming items;
 *  urgent items always fully shown.
 */

import { el } from '../../utils/dom.js';
import { esc, fmtDate } from '../../utils/format.js';

const INITIAL_VISIBLE = 6;

// ── Helpers ───────────────────────────────────────────────────────────────

function _diffDays(rawDate, today) {
  if (!rawDate) return null;
  const d = new Date(String(rawDate).length === 10 ? rawDate + 'T00:00' : rawDate);
  d.setHours(0, 0, 0, 0);
  return Math.round((d - today) / 86_400_000);
}

function _urgencyClass(diffDays) {
  if (diffDays === null) return '';
  if (diffDays === 0)    return 'cc-row--today';
  if (diffDays === 1)    return 'cc-row--tomorrow';
  if (diffDays <= 3)     return 'cc-row--soon';
  return '';
}

function _urgencyBadge(diffDays) {
  if (diffDays === null) return '';
  if (diffDays === 0)    return `<span class="cc-badge cc-badge--today">Hôm nay</span>`;
  if (diffDays === 1)    return `<span class="cc-badge cc-badge--tomorrow">Ngày mai</span>`;
  if (diffDays <= 3)     return `<span class="cc-badge cc-badge--soon">còn ${diffDays}d</span>`;
  return '';
}

function _buildRow(item, diffDays) {
  const ticker  = item.thesis_ticker ?? item.ticker ?? null;
  const desc    = item.description   ?? item.name   ?? 'Catalyst';
  const rawDate = item.expected_date ?? item.expected_at ?? null;
  const dateStr = rawDate ? fmtDate(rawDate) : null;
  const urgCls  = _urgencyClass(diffDays);
  const badge   = _urgencyBadge(diffDays);

  return `<div class="cc-row ${urgCls}">
    ${badge}
    ${ticker ? `<span class="cc-ticker">${esc(ticker)}</span>` : ''}
    <span class="cc-desc">${esc(desc)}</span>
    ${dateStr ? `<span class="cc-date">${dateStr}</span>` : ''}
  </div>`;
}

// ── Main export ───────────────────────────────────────────────────────────

export function renderCatalystCalendar(raw, wrapId = 'catalystList') {
  const wrap = el(wrapId);
  if (!wrap) return;

  const list = Array.isArray(raw) ? raw : (Array.isArray(raw?.items) ? raw.items : []);

  if (!list.length) {
    wrap.innerHTML = `
      <div class="cc-empty">
        <span>📅</span>
        <p>Không có catalyst nào trong 30 ngày tới.</p>
      </div>`;
    return;
  }

  const today = new Date();
  today.setHours(0, 0, 0, 0);

  // Annotate & split
  const urgent   = [];
  const upcoming = [];

  list.forEach(item => {
    const diff = _diffDays(item.expected_date ?? item.expected_at ?? null, today);
    const annotated = { ...item, _diff: diff };
    if (diff !== null && diff >= 0 && diff <= 3) urgent.push(annotated);
    else upcoming.push(annotated);
  });

  // Sort upcoming by date ASC, no-date last
  upcoming.sort((a, b) => {
    if (a._diff === null && b._diff === null) return 0;
    if (a._diff === null) return 1;
    if (b._diff === null) return -1;
    return a._diff - b._diff;
  });

  const hasMore = upcoming.length > INITIAL_VISIBLE;
  const visibleUpcoming = hasMore ? upcoming.slice(0, INITIAL_VISIBLE) : upcoming;
  const hiddenUpcoming  = hasMore ? upcoming.slice(INITIAL_VISIBLE)    : [];

  let html = `<div class="cc-list">`;

  // ── Urgent section
  if (urgent.length) {
    html += `<div class="cc-section-label cc-section-label--urgent">⚡ Sắp đến</div>`;
    html += urgent.map(i => _buildRow(i, i._diff)).join('');
    if (upcoming.length) {
      html += `<div class="cc-divider"></div>`;
    }
  }

  // ── Upcoming section
  if (upcoming.length) {
    if (urgent.length) {
      html += `<div class="cc-section-label">📆 Sắp tới</div>`;
    }
    html += visibleUpcoming.map(i => _buildRow(i, i._diff)).join('');
  }

  // ── Show-more
  if (hasMore) {
    html += `
      <div class="cc-hidden" id="cc-hidden-rows" hidden>
        ${hiddenUpcoming.map(i => _buildRow(i, i._diff)).join('')}
      </div>
      <button class="cc-more-btn" id="cc-more-btn" type="button" aria-expanded="false">
        Xem thêm (${hiddenUpcoming.length})
      </button>`;
  }

  html += `</div>`;
  wrap.innerHTML = html;

  // Wire show-more toggle
  if (hasMore) {
    const btn    = wrap.querySelector('#cc-more-btn');
    const hidden = wrap.querySelector('#cc-hidden-rows');
    btn?.addEventListener('click', () => {
      const expanded = btn.getAttribute('aria-expanded') === 'true';
      if (expanded) {
        hidden.hidden = true;
        btn.setAttribute('aria-expanded', 'false');
        btn.textContent = `Xem thêm (${hiddenUpcoming.length})`;
      } else {
        hidden.hidden = false;
        btn.setAttribute('aria-expanded', 'true');
        btn.textContent = 'Thu gọn';
      }
    });
  }
}
