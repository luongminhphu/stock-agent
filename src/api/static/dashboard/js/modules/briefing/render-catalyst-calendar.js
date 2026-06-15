/**
 * render-catalyst-calendar.js  — Graphical monthly calendar layout
 * Owner  : modules/briefing
 * HTML   : #catalystList
 * CSS    : css/modules/catalyst-calendar.css (.cc-cal-*)
 *
 * Layout:
 *   Header row: "Tháng M / YYYY"  ‹ prev  ›  next
 *   Weekday row: T2 T3 T4 T5 T6 T7 CN
 *   5–6 week grid of day cells with colored dot indicators
 *   Detail popover below calendar (inside same container)
 *
 * Urgency dots: red ≤3 days, amber ≤7 days, blue otherwise
 */

import { el } from '../../utils/dom.js';
import { esc } from '../../utils/format.js';

// ── Constants ──────────────────────────────────────────────────────────────

const WEEKDAY_LABELS = ['T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN'];
// JS getDay(): 0=Sun,1=Mon,…,6=Sat  → we want Mon=0..Sun=6
const JS_TO_GRID_COL = [6, 0, 1, 2, 3, 4, 5]; // [Sun,Mon,Tue,Wed,Thu,Fri,Sat]

const VI_MONTHS = [
  'Tháng 1', 'Tháng 2', 'Tháng 3', 'Tháng 4',
  'Tháng 5', 'Tháng 6', 'Tháng 7', 'Tháng 8',
  'Tháng 9', 'Tháng 10', 'Tháng 11', 'Tháng 12',
];

// ── Module-level state ──────────────────────────────────────────────────────

/** @type {Map<string, Array>} YYYY-MM-DD → catalyst items */
let _catalystMap = new Map();
let _today = null;        // Date (midnight local)
let _todayKey = '';       // YYYY-MM-DD

let _viewYear = 0;
let _viewMonth = 0;       // 0-based

let _selectedKey = null;  // currently-open day
let _wrapId = 'catalystList';

// ── Helpers ─────────────────────────────────────────────────────────────────

/** Parse YYYY-MM-DD to local midnight Date */
function _parseDate(str) {
  if (!str) return null;
  const s = String(str);
  return new Date(s.length === 10 ? s + 'T00:00' : s);
}

/** Format a Date to YYYY-MM-DD */
function _toKey(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

/** Days from today to a YYYY-MM-DD key (negative = past) */
function _diffDays(key) {
  const d = _parseDate(key);
  if (!d) return null;
  return Math.round((d - _today) / 86_400_000);
}

/** Urgency level: 'red' | 'amber' | 'blue' */
function _urgency(key) {
  const diff = _diffDays(key);
  if (diff === null) return 'blue';
  if (diff <= 3)  return 'red';
  if (diff <= 7)  return 'amber';
  return 'blue';
}

/** Vietnamese date header: "15 tháng 6, 2026" */
function _viDateHeader(key) {
  const d = _parseDate(key);
  if (!d) return key;
  return `${d.getDate()} tháng ${d.getMonth() + 1}, ${d.getFullYear()}`;
}

/** Urgency badge text for a YYYY-MM-DD key */
function _urgencyBadge(key) {
  const diff = _diffDays(key);
  if (diff === null) return '';
  if (diff === 0) return `<span class="cc-cal-badge cc-cal-badge--today">Hôm nay</span>`;
  if (diff === 1) return `<span class="cc-cal-badge cc-cal-badge--tomorrow">Ngày mai</span>`;
  if (diff <= 3)  return `<span class="cc-cal-badge cc-cal-badge--soon">còn ${diff}d</span>`;
  return '';
}

/** Number of days in a given month */
function _daysInMonth(year, month) {
  return new Date(year, month + 1, 0).getDate();
}

/** Grid column index (0=Mon … 6=Sun) for a given Date */
function _gridCol(d) {
  return JS_TO_GRID_COL[d.getDay()];
}

// ── Auto-select logic ────────────────────────────────────────────────────────

/** Find the best day to auto-select on initial render */
function _autoSelectKey() {
  // If today has catalysts, use today
  if (_catalystMap.has(_todayKey)) return _todayKey;

  // Otherwise find nearest future catalyst day
  let best = null;
  let bestDiff = Infinity;
  for (const key of _catalystMap.keys()) {
    const diff = _diffDays(key);
    if (diff !== null && diff > 0 && diff < bestDiff) {
      bestDiff = diff;
      best = key;
    }
  }
  return best;
}

// ── Render helpers ───────────────────────────────────────────────────────────

/** Render dot indicators for a day cell */
function _renderDots(items, urgencyLevel) {
  const MAX_DOTS = 3;
  if (items.length === 0) return '';
  if (items.length <= MAX_DOTS) {
    return items.map(() =>
      `<span class="cc-cal-dot cc-cal-dot--${urgencyLevel}"></span>`
    ).join('');
  }
  // More than 3: show 3 dots + overflow label
  const overflow = items.length - MAX_DOTS;
  const dots = Array(MAX_DOTS).fill(
    `<span class="cc-cal-dot cc-cal-dot--${urgencyLevel}"></span>`
  ).join('');
  return dots + `<span class="cc-cal-overflow">+${overflow}</span>`;
}

/** Build the detail panel HTML for a given date key */
function _renderDetail(key) {
  const items = _catalystMap.get(key) || [];
  if (!items.length) return '';

  const dateHeader = _viDateHeader(key);
  const rows = items.map(item => {
    const ticker = item.thesis_ticker ?? item.ticker ?? null;
    const desc   = item.description ?? item.name ?? 'Catalyst';
    return `<div class="cc-cal-detail-row">
      ${ticker ? `<span class="cc-cal-chip">${esc(ticker)}</span>` : ''}
      <span class="cc-cal-detail-desc">${esc(desc)}</span>
    </div>`;
  }).join('');

  const badge = _urgencyBadge(key);

  return `<div class="cc-cal-detail" data-key="${esc(key)}">
    <div class="cc-cal-detail-header">
      <span class="cc-cal-detail-date">${esc(dateHeader)}</span>
      ${badge}
    </div>
    <div class="cc-cal-detail-body">${rows}</div>
  </div>`;
}

/** Build the full calendar grid HTML for current view month */
function _renderGrid() {
  const year  = _viewYear;
  const month = _viewMonth; // 0-based
  const total = _daysInMonth(year, month);

  // First day of month → grid column
  const firstDate = new Date(year, month, 1);
  const startCol  = _gridCol(firstDate);

  const todayYear  = _today.getFullYear();
  const todayMonth = _today.getMonth();
  const todayDay   = _today.getDate();

  let cells = '';

  // Leading empty cells for days before the 1st
  for (let i = 0; i < startCol; i++) {
    cells += `<div class="cc-cal-cell cc-cal-cell--empty"></div>`;
  }

  for (let day = 1; day <= total; day++) {
    const key   = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    const items = _catalystMap.get(key) || [];
    const isToday   = year === todayYear && month === todayMonth && day === todayDay;
    const hasCats   = items.length > 0;
    const isSelected = key === _selectedKey;
    const urg        = hasCats ? _urgency(key) : 'blue';

    const classes = [
      'cc-cal-cell',
      isToday    ? 'cc-cal-cell--today'    : '',
      hasCats    ? 'cc-cal-cell--has-cats' : '',
      isSelected ? 'cc-cal-cell--selected' : '',
    ].filter(Boolean).join(' ');

    const dotsHtml = hasCats ? _renderDots(items, urg) : '';

    cells += `<div class="${classes}" data-key="${esc(key)}" role="${hasCats ? 'button' : 'gridcell'}" tabindex="${hasCats ? '0' : '-1'}" aria-label="${hasCats ? `${day} tháng ${month + 1}: ${items.length} catalyst` : `${day} tháng ${month + 1}`}" aria-pressed="${isSelected}">
      <span class="cc-cal-day-num">${day}</span>
      ${dotsHtml ? `<div class="cc-cal-dots">${dotsHtml}</div>` : ''}
    </div>`;
  }

  // Trailing empty cells to complete the last row (7 columns)
  const filled     = startCol + total;
  const remainder  = filled % 7;
  const trailingCount = remainder === 0 ? 0 : 7 - remainder;
  for (let i = 0; i < trailingCount; i++) {
    cells += `<div class="cc-cal-cell cc-cal-cell--empty"></div>`;
  }

  return cells;
}

// ── Main render ──────────────────────────────────────────────────────────────

function _render() {
  const wrap = el(_wrapId);
  if (!wrap) return;

  const monthLabel = `${VI_MONTHS[_viewMonth]} / ${_viewYear}`;

  const detailHtml = _selectedKey ? _renderDetail(_selectedKey) : '';

  wrap.innerHTML = `
    <div class="cc-cal-shell">
      <div class="cc-cal-header">
        <button class="cc-cal-nav" id="cc-cal-prev" aria-label="Tháng trước" type="button">&#8249;</button>
        <span class="cc-cal-month-label">${esc(monthLabel)}</span>
        <button class="cc-cal-nav" id="cc-cal-next" aria-label="Tháng sau" type="button">&#8250;</button>
      </div>
      <div class="cc-cal-weekdays" role="row" aria-hidden="true">
        ${WEEKDAY_LABELS.map(d => `<div class="cc-cal-wday">${d}</div>`).join('')}
      </div>
      <div class="cc-cal-grid" role="grid" aria-label="${esc(monthLabel)}">
        ${_renderGrid()}
      </div>
      ${detailHtml}
    </div>`;

  _bindEvents(wrap);
}

function _bindEvents(wrap) {
  // Prev / Next month navigation
  wrap.querySelector('#cc-cal-prev')?.addEventListener('click', () => {
    _viewMonth--;
    if (_viewMonth < 0) { _viewMonth = 11; _viewYear--; }
    _render();
  });
  wrap.querySelector('#cc-cal-next')?.addEventListener('click', () => {
    _viewMonth++;
    if (_viewMonth > 11) { _viewMonth = 0; _viewYear++; }
    _render();
  });

  // Day cell click
  wrap.querySelectorAll('.cc-cal-cell--has-cats').forEach(cell => {
    const handler = (e) => {
      const key = cell.dataset.key;
      if (!key) return;
      if (_selectedKey === key) {
        // Toggle off
        _selectedKey = null;
      } else {
        _selectedKey = key;
        // Navigate to the month containing that key if needed
        const d = _parseDate(key);
        if (d) {
          const newYear  = d.getFullYear();
          const newMonth = d.getMonth();
          if (newYear !== _viewYear || newMonth !== _viewMonth) {
            _viewYear  = newYear;
            _viewMonth = newMonth;
          }
        }
      }
      _render();
    };

    cell.addEventListener('click', handler);
    cell.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        handler(e);
      }
    });
  });
}

// ── Public export ────────────────────────────────────────────────────────────

export function renderCatalystCalendar(raw, wrapId = 'catalystList') {
  _wrapId = wrapId;
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

  // Build module-level map
  _catalystMap = new Map();
  _today = new Date();
  _today.setHours(0, 0, 0, 0);
  _todayKey = _toKey(_today);

  list.forEach(item => {
    const rawDate = item.expected_date ?? item.expected_at ?? null;
    if (!rawDate) return;
    const key = String(rawDate).slice(0, 10); // take YYYY-MM-DD
    if (!_catalystMap.has(key)) _catalystMap.set(key, []);
    _catalystMap.get(key).push(item);
  });

  // Default view: current month
  _viewYear  = _today.getFullYear();
  _viewMonth = _today.getMonth();

  // Auto-select
  _selectedKey = _autoSelectKey();

  // If auto-selected key is in a different month, jump to it
  if (_selectedKey) {
    const d = _parseDate(_selectedKey);
    if (d) {
      _viewYear  = d.getFullYear();
      _viewMonth = d.getMonth();
    }
  }

  _render();
}
