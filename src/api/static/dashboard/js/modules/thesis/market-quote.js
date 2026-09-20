/**
 * market-quote.js
 * Owner: modules/thesis
 * Responsibility: fetch live market quote cho ticker của thesis,
 *   render quote strip hiển thị ngay dưới detail-head.
 *
 * Public API:
 *   fetchQuote(ticker)  → Promise<QuoteData|null>  (với graceful fallback)
 *   fetchContext(ticker) → Promise<TickerContext|QuoteData|null>
 *       Wave U2b: ưu tiên /market/context (quote + MA/RSI/ATR/vol/52w + trend +
 *       source_quality — cùng bối cảnh AI review đang đọc), fallback /market/quote.
 *   renderQuoteStrip(quote, thesis)  → string (HTML fragment)
 *   quoteStripSkeletonHTML()         → string
 */

import { getJson } from '../../api/client.js?v=1';
import { fmt, esc } from '../../utils/format.js?v=1';
import {
  movementClass,
  fmtSignedPct,
  dataQualityTagHTML,
  trendTagHTML,
} from '../../utils/market-badges.js?v=1';

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

/**
 * Wave U2b: fetch TickerContext; nếu endpoint lỗi (market đóng / thiếu OHLCV)
 * fallback quote thuần để strip vẫn có giá. Trả null khi cả hai đều lỗi.
 * @param {string} ticker
 * @returns {Promise<object|null>}
 */
export async function fetchContext(ticker) {
  if (!ticker) return null;
  const sym = encodeURIComponent(ticker.toUpperCase());
  try {
    return await getJson(`/api/v1/market/context/${sym}`);
  } catch {
    const q = await fetchQuote(ticker);
    return q ? { ...q, source_quality: 'quote' } : null;
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
  // Quy tắc màu HSC: trần / sàn / tăng / giảm / tham chiếu (0% = vàng, không phải xanh)
  if (quote.is_ceiling) return 'quote-price--ceiling';
  if (quote.is_floor)   return 'quote-price--floor';
  if (quote.change_pct == null) return '';
  if (quote.change_pct > 0) return 'quote-price--up';
  if (quote.change_pct < 0) return 'quote-price--down';
  return 'quote-price--ref';
}

function changePctClass(pct) {
  if (pct == null) return '';
  if (pct > 0) return 'quote-change--up';
  if (pct < 0) return 'quote-change--down';
  return 'quote-change--flat';
}

// ─── Render ──────────────────────────────────────────────────────────────────

/**
 * Render quote strip HTML.
 * @param {object|null} quote  — QuoteResponse từ API (null = không có data)
 * @param {object}      thesis — thesis object { entry_price, target_price, stop_loss }
 * @returns {string}
 */
/**
 * Hàng chỉ báo D1 (chỉ khi context có indicator). Mỗi chip: label + giá trị.
 * Số so với giá (MA20/MA50) mang màu biến động tương ứng; RSI/ATR/Vol trung tính.
 */
function indicatorsRowHTML(ctx) {
  if (!ctx || ctx.ma20 == null && ctx.rsi14 == null && ctx.atr14 == null) return '';
  const price = ctx.price;
  const vsMa = (ma) => {
    if (ma == null || !price) return { cls: 'mv-none', txt: '' };
    const pct = (price / ma - 1) * 100;
    return { cls: movementClass({ change_pct: pct }), txt: fmtSignedPct(pct, 1) };
  };
  const ma20 = vsMa(ctx.ma20);
  const ma50 = vsMa(ctx.ma50);
  const rsi = ctx.rsi14;
  const rsiNote = rsi == null ? '' : rsi >= 70 ? ' \u00b7 quá mua' : rsi <= 30 ? ' \u00b7 quá bán' : '';
  const atrPct = ctx.atr14 != null && price ? (ctx.atr14 / price * 100).toFixed(1) + '%' : null;
  const vol = ctx.vol_ratio_20;
  const chip = (label, val, cls = '', title = '') => `
        <div class="quote-chip" ${title ? `title="${esc(title)}"` : ''}>
          <span class="qc-label">${label}</span>
          <span class="qc-val ${cls}">${val}</span>
        </div>`;
  return `
      <div class="quote-strip-indicators" aria-label="Chỉ báo kỹ thuật D1">
        ${chip('MA20', ctx.ma20 != null ? `${fmt(ctx.ma20)} <small class="${ma20.cls}">${esc(ma20.txt)}</small>` : '\u2014', '', 'Giá so với trung bình 20 phiên')}
        ${chip('MA50', ctx.ma50 != null ? `${fmt(ctx.ma50)} <small class="${ma50.cls}">${esc(ma50.txt)}</small>` : '\u2014', '', 'Giá so với trung bình 50 phiên')}
        ${chip('RSI14', rsi != null ? `${Number(rsi).toFixed(1)}<small>${rsiNote}</small>` : '\u2014')}
        ${chip('ATR14', ctx.atr14 != null ? `${fmt(ctx.atr14)}${atrPct ? ` <small>(${atrPct})</small>` : ''}` : '\u2014', '', 'Biên độ dao động trung bình 14 phiên')}
        ${chip('Vol/TB20', vol != null ? `${Number(vol).toFixed(2)}x` : '\u2014', vol != null && vol >= 1.5 ? 'mv-up' : '', 'Khối lượng phiên gần nhất so với trung bình 20 phiên')}
        ${ctx.hi_52w != null ? chip('52 tuần', `${fmt(ctx.lo_52w)} \u2013 ${fmt(ctx.hi_52w)}`) : ''}
        <div class="quote-strip-tags">
          ${trendTagHTML(ctx.trend_state)}
          ${dataQualityTagHTML(ctx.source_quality, ctx.as_of)}
        </div>
      </div>`;
}

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

      <!-- Meta chips: O/H/L + thesis context (Khối lượng removed — always — outside trading hours) -->
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
        ${quote.ma20 == null && quote.source_quality ? `<div class="quote-strip-tags">${dataQualityTagHTML(quote.source_quality, quote.as_of)}</div>` : ''}
      </div>

      ${indicatorsRowHTML(quote)}

    </div>`;
}
