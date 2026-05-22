/**
 * watchlist-renderer.js
 * Owner: modules/watchlist
 * Responsibility: pure DOM render — toàn bộ watchlist section
 * Rule: không gọi API, không có side-effects ngoài DOM.
 *       nhận callbacks từ loader thông qua options object.
 */

import { esc, fmt, fmtDate } from '../../utils/format.js';

/**
 * Human-readable labels cho signal_type từ SignalEngine.
 * Fallback: raw signal_type string nếu không có trong map.
 */
const SIGNAL_LABELS = {
  BREAKOUT:          'Breakout',
  RISK_SPIKE:        'Risk Spike',
  STRONG_MOVE:       'Strong Move',
  THESIS_DIVERGENCE: 'Thesis Div.',
  TREND_REVERSAL:    'Reversal',
  STOP_LOSS:         'Stop Loss',
};

/**
 * Render toàn bộ watchlist vào container.
 *
 * @param {HTMLElement} container
 * @param {Array}       items      - enriched watchlist items ({...WatchlistItemResponse, quote})
 * @param {{ onRemove, onScan, onAdd, signalsMap }} options - action callbacks + scan context
 *   signalsMap: { [ticker]: SignalReport[] } — populated after a manual scan,
 *               empty object on first load (no scan yet this session).
 */
export function renderWatchlist(container, items, { onRemove, onScan, onAdd, signalsMap = {} } = {}) {
  container.innerHTML = '';

  const scanResultId = 'wlScanResult';

  // ── Toolbar ──────────────────────────────────────────────────────────────
  const toolbar = document.createElement('div');
  toolbar.className = 'wl-toolbar';
  toolbar.innerHTML = `
    <span class="muted" style="font-size:0.8rem">${items.length} mã theo dõi</span>
    <div class="wl-toolbar-actions">
      <button type="button" id="wlAddBtn" class="ghost-btn" style="font-size:0.82rem;min-height:34px;padding:0 12px;">
        + Thêm mã
      </button>
      <button type="button" id="wlScanBtn" class="icon-text-btn wl-scan-btn">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="11" cy="11" r="8"/>
          <path d="m21 21-4.35-4.35"/>
        </svg>
        Scan now
      </button>
    </div>
  `;
  container.appendChild(toolbar);

  // ── Empty state ───────────────────────────────────────────────────────────
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'wl-empty';
    empty.innerHTML = `
      <strong>Watchlist trống</strong>
      <span>Nhấn "+ Thêm mã" để bắt đầu theo dõi cổ phiếu.</span>
    `;
    container.appendChild(empty);
  } else {
    // ── Card grid ───────────────────────────────────────────────────────────
    const grid = document.createElement('div');
    grid.className = 'wl-grid';

    for (const item of items) {
      const signalReports = signalsMap[item.ticker] ?? [];
      grid.appendChild(buildCard(item, onRemove, signalReports));
    }
    container.appendChild(grid);
  }

  // ── Scan result banner ────────────────────────────────────────────────────
  const scanResult = document.createElement('div');
  scanResult.id = scanResultId;
  scanResult.className = 'wl-scan-result hidden';
  container.appendChild(scanResult);

  // ── Wire scan button ────────────────────────────────────────────────────────
  const scanBtn = container.querySelector('#wlScanBtn');
  if (scanBtn && onScan) {
    scanBtn.addEventListener('click', () => onScan(scanResult, scanBtn));
  }

  // ── Wire add button → inline modal ──────────────────────────────────────────
  const addBtn = container.querySelector('#wlAddBtn');
  if (addBtn && onAdd) {
    addBtn.addEventListener('click', () => {
      const dialog = document.getElementById('watchlistAddModal');
      dialog?.showModal();
    });
  }
}

/**
 * Build DOM cho một watchlist card.
 *
 * @param {object}    item          - enriched watchlist item
 * @param {Function}  onRemove
 * @param {Array}     signalReports - SignalReport[] từ scan session hiện tại ([] nếu chưa scan)
 * @returns {HTMLElement}
 */
function buildCard(item, onRemove, signalReports = []) {
  const q = item.quote;

  // Price + change
  const priceHtml = q?.formatted_price
    ? `<span class="wl-price">${esc(q.formatted_price)}</span>`
    : q?.price != null
      ? `<span class="wl-price">${fmt(q.price)}</span>`
      : `<span class="wl-price loading">— giá —</span>`;

  const changeClass = !q?.change_pct
    ? 'flat'
    : q.change_pct > 0 ? 'up' : 'down';
  const changeSign  = q?.change_pct > 0 ? '+' : '';
  const changeHtml  = q?.change_pct != null
    ? `<span class="wl-change ${changeClass}">${changeSign}${Number(q.change_pct).toFixed(2)}%</span>`
    : '';

  // Ceiling / floor badges
  const ceilBadge  = q?.is_ceiling ? '<span class="wl-ceil-badge">TRẦN</span>'  : '';
  const floorBadge = q?.is_floor   ? '<span class="wl-floor-badge">SÀN</span>' : '';

  // #3 FIX: thesis badge — clickable nếu có thesis_id
  // dispatch 'navigate:thesis' event lên document → app.js listener xử lý
  const thesisBadge = item.thesis_id
    ? `<span class="wl-thesis-badge wl-thesis-badge--link"
           role="button"
           tabindex="0"
           title="Xem Thesis #${item.thesis_id}"
           data-thesis-id="${item.thesis_id}">thesis ↗</span>`
    : '';

  // Signal tags — actionable only, sorted by strength desc, max 3
  const tagsHtml = signalReports
    .filter(r => r.actionable)
    .sort((a, b) => b.strength - a.strength)
    .slice(0, 3)
    .map(r => {
      const label = SIGNAL_LABELS[r.signal_type] ?? r.signal_type;
      const typeSlug = r.signal_type.toLowerCase().replace(/_/g, '-');
      return `<span class="wl-signal-tag wl-signal-${typeSlug}" title="${esc(r.description ?? '')}">${esc(label)}</span>`;
    })
    .join('');

  const card = document.createElement('div');
  card.className = 'wl-card';
  card.dataset.ticker = item.ticker;
  card.innerHTML = `
    <div class="wl-card-head">
      <span class="wl-ticker">${esc(item.ticker)}</span>
      <div class="wl-card-badges">
        ${thesisBadge}
        ${tagsHtml}
      </div>
    </div>
    <div class="wl-price-row">
      ${priceHtml}
      ${changeHtml}
      ${ceilBadge}
      ${floorBadge}
    </div>
    ${item.note ? `<p class="wl-note">${esc(item.note)}</p>` : ''}
    <div class="wl-card-foot">
      <span class="wl-added-at">+${fmtDate(item.added_at)}</span>
      <button class="wl-remove-btn" data-ticker="${esc(item.ticker)}" aria-label="Xóa ${esc(item.ticker)} khỏi watchlist" title="Xóa">
        ✕
      </button>
    </div>
  `;

  // Wire remove button
  if (onRemove) {
    card.querySelector('.wl-remove-btn')?.addEventListener('click', (e) => {
      e.stopPropagation();
      onRemove(item.ticker);
    });
  }

  // #3 FIX: wire thesis badge click — dispatch event, app.js handles navigation
  if (item.thesis_id) {
    const badge = card.querySelector('.wl-thesis-badge--link');
    if (badge) {
      const navigate = () => {
        document.dispatchEvent(new CustomEvent('navigate:thesis', {
          detail: { thesisId: item.thesis_id },
        }));
      };
      badge.addEventListener('click', (e) => { e.stopPropagation(); navigate(); });
      badge.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate(); }
      });
    }
  }

  return card;
}
