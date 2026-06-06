/**
 * modules/market/catalyst-urgent.js
 * Owner: market segment (data context) / dashboard adapter (render)
 *
 * Renders a compact chip strip of catalysts due within 72 hours into
 * #catalystUrgentStrip. Reuses data already fetched by loadDashboard()
 * (catalysts/upcoming?days=30) — no extra API call needed.
 *
 * Design:
 *   - Strip is purely additive: brief panel still shows the full calendar.
 *   - Only shows items with expected_date within 72h (today + 2 days).
 *   - Max 6 chips; overflow badge "+N khác" if more.
 *   - Each chip is colour-coded: today=danger, tomorrow=warning, 2d=brand.
 *   - Hidden when no urgent catalysts (graceful degradation).
 */

const ELEMENT_ID  = 'catalystUrgentStrip';
const URGENT_DAYS = 3;   // 0–2 days ahead (≤72h)
const MAX_SHOWN   = 6;

/**
 * Render urgent catalyst strip from catalyst list already fetched by dashboard.
 * @param {Array} catalysts  raw items from GET /readmodel/dashboard/catalysts/upcoming
 */
export function renderCatalystUrgentStrip(catalysts) {
  const wrap = document.getElementById(ELEMENT_ID);
  if (!wrap) return;

  const list = Array.isArray(catalysts)
    ? catalysts
    : (Array.isArray(catalysts?.items) ? catalysts.items : []);

  if (!list.length) {
    wrap.classList.add('hidden');
    return;
  }

  const today = new Date();
  today.setHours(0, 0, 0, 0);

  // Filter items due within 72h and compute diffDays
  const urgent = [];
  for (const item of list) {
    const rawDate = item.expected_date ?? item.expected_at ?? null;
    if (!rawDate) continue;
    const d = new Date(String(rawDate).length === 10 ? rawDate + 'T00:00' : rawDate);
    d.setHours(0, 0, 0, 0);
    const diff = Math.round((d - today) / 86_400_000);
    if (diff >= 0 && diff < URGENT_DAYS) {
      urgent.push({ ...item, _diff: diff });
    }
  }

  if (!urgent.length) {
    wrap.classList.add('hidden');
    return;
  }

  // Sort: today first, then tomorrow, then 2d
  urgent.sort((a, b) => a._diff - b._diff);

  const shown    = urgent.slice(0, MAX_SHOWN);
  const overflow = urgent.length - shown.length;

  wrap.classList.remove('hidden');
  wrap.innerHTML = `
    <span class="cu-strip__label">⚡ Catalyst sắp đến</span>
    ${shown.map(item => _buildChip(item)).join('')}
    ${overflow > 0 ? `<span class="cu-strip__more">+${overflow} khác</span>` : ''}
  `;
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

function _buildChip(item) {
  const ticker = item.thesis_ticker ?? item.ticker ?? '—';
  const desc   = item.description ?? item.name ?? '';
  const diff   = item._diff;

  const urgencyCls = diff === 0 ? 'cu-chip--today'
    : diff === 1               ? 'cu-chip--tomorrow'
    :                            'cu-chip--soon';

  const dayLabel = diff === 0 ? 'Hôm nay'
    : diff === 1              ? 'Ngày mai'
    :                           `${diff}d`;

  const shortDesc = desc.length > 40 ? desc.slice(0, 38) + '…' : desc;
  const title = `${ticker} · ${desc}`;

  return `
    <div class="cu-chip ${urgencyCls}" title="${_esc(title)}">
      <span class="cu-chip__ticker">${_esc(ticker)}</span>
      <span class="cu-chip__day">${dayLabel}</span>
      ${shortDesc ? `<span class="cu-chip__desc">${_esc(shortDesc)}</span>` : ''}
    </div>`;
}

function _esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
