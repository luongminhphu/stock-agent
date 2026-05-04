/**
 * market-quote.js
 * Owner: modules/thesis
 * Responsibility: fetch live market quote cho ticker của thesis,
 *   render quote strip hiển thị ngay dưới detail-head.
 *
 * Public API:
 *   fetchQuote(ticker)  → Promise<QuoteData|null>  (với graceful fallback)
 *   renderQuoteStrip(quote, thesis)  → string (HTML fragment)
 *   quoteStripSkeletonHTML()         → string
 */

import { getJson } from '../../api/client.js';
import { fmt, esc } from '../../utils/format.js';

// ─── API ─────────────────────────────────────────────────────────────────────

/**
 * Fetch live quote từ /api/v1/market/quote/{ticker}.
 * Trả về null (không throw) nếu API lỗi / 502 — UI fallback gracefully.
 * @param {string} ticker
 * @returns {Promise<object|null>}
 */
export async function fetchQuote(ticker) {
  if (!ticker) return null;
  try {
    return await getJson(`/api/v1/market/quote/${encodeURIComponent(ticker.toUpperCase())}`);
  } catch {
    return null;
  }
}

// ─── Skeleton ────────────────────────────────────────────────────────────────

export function quoteStripSkeletonHTML() {
  return `
    <div class="quote-strip quote-strip--loading" aria-busy="true">
      <div class="quote-strip-price">
        <div class="skel skel-text" style="width:90px;height:1.6em;"></div>
        <div class="skel skel-badge" style="width:72px;"></div>
      </div>
      <div class="quote-strip-meta">
        <div class="skel skel-text" style="width:60px;"></div>
        <div class="skel skel-text" style="width:60px;"></div>
        <div class="skel skel-text" style="width:60px;"></div>
        <div class="skel skel-text" style="width:60px;"></div>
      </div>
    </div>`;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Tính upside / downside còn lại so với entry price của thesis.
 * @param {number|null} currentPrice
 * @param {number|null} entryPrice
 * @param {number|null} targetPrice
 * @returns {{ vsEntry: string|null, toTarget: string|null, vsEntryUp: boolean }}
 */
function calcThesisContext(currentPrice, entryPrice, targetPrice) {
  if (!currentPrice) return { vsEntry: null, toTarget: null, vsEntryUp: true };
  let vsEntry = null;
  let vsEntryUp = true;
  if (entryPrice && entryPrice > 0) {
    const pct = ((currentPrice - entryPrice) / entryPrice) * 100;
    vsEntryUp = pct >= 0;
    vsEntry = (pct >= 0 ? '+' : '') + pct.toFixed(1) + '% vs entry';
  }
  let toTarget = null;
  if (targetPrice && targetPrice > 0) {
    const rem = ((targetPrice - currentPrice) / currentPrice) * 100;
    toTarget = (rem >= 0 ? '+' : '') + rem.toFixed(1) + '% to target';
  }
  return { vsEntry, toTarget, vsEntryUp };
}

/**
 * Label + CSS class cho mức giá (ceiling / floor / normal).
 */
function priceStateClass(quote) {
  if (quote.is_ceiling) return 'quote-price--ceiling';
  if (quote.is_floor)   return 'quote-price--floor';
  if (quote.change_pct == null) return '';
  return quote.change_pct >= 0 ? 'quote-price--up' : 'quote-price--down';
}

function changePctClass(pct) {
  if (pct == null) return '';
  if (pct > 0) return 'quote-change--up';
  if (pct < 0) return 'quote-change--down';
  return 'quote-change--flat';
}

/**
 * Định dạng volume: 1.2M, 850K, …
 */
function fmtVol(v) {
  if (v == null) return '—';
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + 'M';
  if (v >= 1_000)     return (v / 1_000).toFixed(0) + 'K';
  return String(v);
}

// ─── Render ──────────────────────────────────────────────────────────────────

/**
 * Render quote strip HTML.
 * @param {object|null} quote  — QuoteResponse từ API (null = không có data)
 * @param {object}      thesis — thesis object { entry_price, target_price, stop_loss }
 * @returns {string}
 */
export function renderQuoteStrip(quote, thesis) {
  // Nếu không lấy được quote → strip nhỏ với placeholder
  if (!quote) {
    return `
      <div class="quote-strip quote-strip--unavailable">
        <span class="quote-unavailable-label">Giá thị trường không khả dụng</span>
      </div>`;
  }

  const pClass = priceStateClass(quote);
  const cClass = changePctClass(quote.change_pct);
  const { vsEntry, toTarget, vsEntryUp } = calcThesisContext(
    quote.price, thesis?.entry_price, thesis?.target_price
  );

  // Volume bar: tỷ lệ so với 5M cổ phiếu (relative indicator)
  const volRatio = Math.min(1, (quote.volume ?? 0) / 5_000_000);
  const volBarPct = (volRatio * 100).toFixed(0);

  // Formatted change pct
  const changePctStr = quote.change_pct != null
    ? (quote.change_pct >= 0 ? '+' : '') + quote.change_pct.toFixed(2) + '%'
    : '—';
  const changeStr = quote.change != null
    ? (quote.change >= 0 ? '+' : '') + fmt(Math.abs(quote.change)) + '₫'
    : '';

  // Ceiling / Floor special label
  let specialLabel = '';
  if (quote.is_ceiling) specialLabel = `<span class="quote-badge-ceiling">TRẦN</span>`;
  else if (quote.is_floor) specialLabel = `<span class="quote-badge-floor">SÀN</span>`;

  return `
    <div class="quote-strip" data-ticker="${esc(quote.ticker)}">

      <!-- Giá + % change -->
      <div class="quote-strip-price">
        <span class="quote-price ${pClass}">${esc(quote.formatted_price ?? fmt(quote.price) + '₫')}</span>
        ${specialLabel}
        <span class="quote-change ${cClass}">
          ${esc(changePctStr)}
          ${changeStr ? `<span class="quote-change-abs">(${esc(changeStr)})</span>` : ''}
        </span>
      </div>

      <!-- Meta chips: O/H/L + vol + thesis context -->
      <div class="quote-strip-meta">
        <div class="quote-chip">
          <span class="qc-label">Mở cửa</span>
          <span class="qc-val">${quote.open != null ? fmt(quote.open) + '₫' : '—'}</span>
        </div>
        <div class="quote-chip">
          <span class="qc-label">Cao</span>
          <span class="qc-val quote-price--up">${quote.high != null ? fmt(quote.high) + '₫' : '—'}</span>
        </div>
        <div class="quote-chip">
          <span class="qc-label">Thấp</span>
          <span class="qc-val quote-price--down">${quote.low != null ? fmt(quote.low) + '₫' : '—'}</span>
        </div>
        <div class="quote-chip">
          <span class="qc-label">Khối lượng</span>
          <span class="qc-val">${fmtVol(quote.volume)}</span>
          <div class="quote-vol-bar"><div class="quote-vol-fill" style="width:${volBarPct}%"></div></div>
        </div>
        ${vsEntry ? `
        <div class="quote-chip quote-chip--thesis ${vsEntryUp ? 'thesis-up' : 'thesis-down'}">
          <span class="qc-label">So entry</span>
          <span class="qc-val">${esc(vsEntry)}</span>
        </div>` : ''}
        ${toTarget ? `
        <div class="quote-chip quote-chip--thesis">
          <span class="qc-label">Upside còn lại</span>
          <span class="qc-val">${esc(toTarget)}</span>
        </div>` : ''}
      </div>

    </div>`;
}
